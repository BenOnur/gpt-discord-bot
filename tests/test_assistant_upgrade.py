import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import sqlite3
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.assistant_memory import AssistantMemory, explicit_memory_request
from bot.archive_access import open_archive, allowed_channels
from bot.safe_http import public_target, fetch_public
from bot.llm.tools.discord_ctx import set_current_discord_context
from bot.llm.tools import reminder
from bot.llm.tools.manage_reminders import manage_reminders


def context(uid=10, channel_id=20, mid=1):
    public = Obj(id=20, permissions_for=lambda _: Obj(view_channel=True, read_message_history=True))
    private = Obj(id=21, permissions_for=lambda person: Obj(view_channel=getattr(person,"id",0)==10, read_message_history=True))
    guild = Obj(id=30, default_role=Obj(id=0), text_channels=[public,private],threads=[])
    return {"guild":guild,"channel": public if channel_id==20 else private,"author":Obj(id=uid),
            "message":Obj(id=mid,jump_url=f"https://discord.com/channels/30/{channel_id}/{mid}"),"config":{}}


class AssistantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config={"memory":{"data_dir":self.temp.name},"permissions":{"users":{"admin_ids":[10]}}}
        self.memory=AssistantMemory(self.config)

    def test_explicit_turkish_syntax(self):
        for text in ("<@123> şu kuralı kaydet: ciddi sorulara kısa cevap ver", "kural kaydet ciddi sorulara kısa cevap ver"):
            self.assertEqual(explicit_memory_request(text),("rule","ciddi sorulara kısa cevap ver"))
        self.assertEqual(explicit_memory_request("skill oluştur: linki oku sonra özetle")[0],"skill")
        self.assertIsNone(explicit_memory_request('Ahmet dedi ki şu kuralı kaydet: test'))

    def test_persists_deduplicates_and_retrieves_next_request(self):
        ctx=context()
        entry=self.memory.save(ctx,"rule","Ciddi sorularda sakin yanıt ver",shared=True)
        self.assertEqual(self.memory.save(ctx,"rule","duplicate",shared=True),entry)
        reopened=AssistantMemory(self.config)
        self.assertIn("Ciddi",reopened.context(context(11,mid=2),"nasıl"))
        self.assertTrue(list(self.memory.documents.rglob("*.md")))

    def test_personal_and_channel_scope_and_shared_write_authority(self):
        self.memory.save(context(11),"fact","Kahveyi şekersiz severim")
        self.assertFalse(self.memory.entries(context(12)))
        self.memory.save(context(10,21,mid=2),"fact","Private preference")
        self.assertFalse(self.memory.entries(context(10)))
        with self.assertRaises(PermissionError):
            self.memory.save(context(11,mid=3),"rule","Herkes için kural",shared=True)

    def test_revision_and_forget_require_owner(self):
        entry=self.memory.save(context(11),"fact","Eski tercih")
        with self.assertRaises(PermissionError):
            self.memory.change(context(12),entry,"Yanlış düzeltme")
        self.memory.change(context(11),entry,"Yeni tercih")
        self.assertEqual(self.memory.entries(context(11))[0]["revision"],2)
        self.memory.change(context(11),entry)
        self.assertFalse(self.memory.entries(context(11)))

    def test_repeated_success_promotes_skill_but_same_message_does_not(self):
        self.memory.learn_workflow(context(),["read_url","web_search"],"şu linki araştır")
        self.memory.learn_workflow(context(),["read_url","web_search"],"şu linki araştır")
        self.assertFalse(self.memory.entries(context()))
        self.memory.learn_workflow(context(mid=2),["read_url","web_search"],"başka linki araştır")
        rows=self.memory.entries(context())
        self.assertEqual(rows[0]["kind"],"skill")
        self.assertIn("read_url → web_search",rows[0]["body"])

    def test_tool_retry_reuses_effect_without_executing_twice(self):
        from bot.llm.tools.registry import execute_tool_call
        from bot.llm.tools._types import ToolEntry
        ctx=context()
        ctx.update(assistant_memory=self.memory,successful_tools=[])
        set_current_discord_context(ctx)
        fn=MagicMock(return_value="Hatırlatıcı kuruldu")
        registry={"set_reminder":ToolEntry(schema={},fn=fn)}
        first=execute_tool_call("set_reminder",{"duration_minutes":2},registry)
        self.assertEqual(execute_tool_call("set_reminder",{"duration_minutes":2},registry),first)
        fn.assert_called_once()

    def test_secret_not_stored(self):
        with self.assertRaises(ValueError):
            self.memory.save(context(),"fact","key gsk_"+"x"*30)

    def test_public_workflow_can_learn_across_members_without_copying_prompt(self):
        self.memory.learn_workflow(context(11,mid=1),["read_url","web_search"],"private preference in request")
        self.memory.learn_workflow(context(12,mid=2),["read_url","web_search"],"another request")
        rows=self.memory.entries(context(13))
        self.assertEqual(rows[0]["owner"],0)
        self.assertNotIn("private preference",rows[0]["body"])


class ArchiveScopeTests(unittest.TestCase):
    def test_all_queries_scoped_and_private_does_not_flow_to_public(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"archive.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE messages(id INTEGER, guild INTEGER,channel INTEGER,content TEXT)")
                db.executemany("INSERT INTO messages VALUES(?,?,?,?)",[(1,30,20,"public"),(2,30,21,"private"),(3,31,20,"other server")])
                db.execute("CREATE TABLE user_profiles(profile_dossier TEXT)")
                db.execute("INSERT INTO user_profiles VALUES('secret cached dossier')")
            set_current_discord_context(context())
            db=open_archive(path)
            self.assertEqual([r["content"] for r in db.execute("SELECT * FROM messages")],["public"])
            self.assertEqual(db.execute("SELECT * FROM user_profiles").fetchall(),[])
            db.close()
            self.assertEqual(allowed_channels(context(channel_id=21)),{20,21})
            set_current_discord_context({})
            db=open_archive(path)
            self.assertEqual(db.execute("SELECT * FROM messages").fetchall(),[])
            db.close()


class SafeHTTPTests(unittest.TestCase):
    def test_internal_addresses_credentials_and_ports_rejected(self):
        for ip in ("127.0.0.1","10.0.0.1","169.254.169.254","::1","fc00::1"):
            with patch("socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(ip,443))]):
                with self.assertRaises(ValueError):
                    public_target("https://example.com")
        for url in ("file:///etc/passwd","https://user:pass@example.com","http://example.com:8799"):
            with self.assertRaises(ValueError):
                public_target(url)

    def test_redirect_to_internal_is_not_requested(self):
        response=MagicMock(status=302,headers={"Location":"http://127.0.0.1/"})
        pool=MagicMock()
        pool.urlopen.return_value=response
        with patch("bot.safe_http.public_target",side_effect=[(Obj(scheme="https",hostname="example.com",port=None,path="/",query="",netloc="example.com"),"93.184.216.34"),ValueError("internal")]),patch("urllib3.HTTPSConnectionPool",return_value=pool):
            with self.assertRaises(ValueError):
                fetch_public("https://example.com")
        pool.urlopen.assert_called_once()


class BudgetAndToolSelectionTests(unittest.TestCase):
    def test_warm_retrieval_accepts_structured_and_json_results_only(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
        from memory_retrieval_server import search_payload
        self.assertEqual(search_payload({"structuredContent":{"results":[]}}),{"results":[]})
        self.assertEqual(search_payload({"content":[{"type":"text","text":'{"results":[]}'}]}),{"results":[]})
        with self.assertRaises(RuntimeError):
            search_payload({"isError":True,"structuredContent":{"results":[]}})

    def test_shared_budget_prevents_overbooking_and_background_competition(self):
        from bot.llm.quota import reserve
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"budget.sqlite3"
            reserve(path,"openai/gpt-oss-120b",4000,"interactive",now=100)
            with self.assertRaises(RuntimeError):
                reserve(path,"openai/gpt-oss-120b",4000,"interactive",now=101)
            with self.assertRaises(RuntimeError):
                reserve(path,"openai/gpt-oss-120b",1000,"background",now=102)
            reserve(path,"openai/gpt-oss-120b",4000,"background",now=162)

    def test_small_relevant_tool_menu(self):
        from bot.llm.tool_selection import select_tools
        available=["web_search","get_current_time","manage_memory","get_member_info","set_reminder","server_trivia"]
        tools=select_tools("şu an dinlediğim şarkıyı araştır",available)
        self.assertIn("get_member_info",tools)
        self.assertNotIn("server_trivia",tools)

    def test_encrypted_backup_round_trip_and_tamper_detection(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
        from backup_crypto import encrypt_backup,decrypt_backup
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/"key"
            key.write_bytes(b"k"*32)
            plain=Path(folder)/"test.tar.gz"
            plain.write_bytes(b"test backup content")
            encrypted=encrypt_backup(plain,key)
            self.assertFalse(plain.exists())
            self.assertEqual(decrypt_backup(encrypted,key),b"test backup content")
            data=encrypted.read_bytes()
            encrypted.write_bytes(data[:-1]+bytes([data[-1]^1]))
            with self.assertRaises(Exception):
                decrypt_backup(encrypted,key)


class ReminderUpgradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patcher=patch.object(reminder,"_DB_PATH",Path(self.temp.name)/"reminders.sqlite3")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.scheduler=MagicMock()
        self.ctx=context()
        self.ctx.update(bot=Obj(loop=Obj(is_running=lambda:False)),scheduler=self.scheduler)
        set_current_discord_context(self.ctx)

    async def test_failed_delivery_stays_pending_and_retries(self):
        reminder.set_reminder(1,"test")
        bot=Obj(get_channel=lambda _:Obj(send=AsyncMock(side_effect=RuntimeError("offline"))))
        await reminder._send_reminder(bot,20,10,"test",1)
        with reminder._get_db() as db:
            row=db.execute("SELECT completed,status,attempts FROM reminders WHERE id=1").fetchone()
        self.assertEqual(tuple(row),(0,"pending",1))
        self.assertGreaterEqual(self.scheduler.add_job.call_count,2)

    async def test_success_marks_sent_and_duplicate_source_is_not_created(self):
        reminder.set_reminder(1,"test")
        reminder.set_reminder(1,"same source retry")
        send=AsyncMock()
        bot=Obj(get_channel=lambda _:Obj(send=send))
        await reminder._send_reminder(bot,20,10,"test",1)
        await reminder._send_reminder(bot,20,10,"test",1)
        send.assert_awaited_once()
        with reminder._get_db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reminders").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT status FROM reminders").fetchone()[0],"sent")

    async def test_only_owner_can_cancel(self):
        reminder.set_reminder(1,"test")
        stranger=context(11)
        set_current_discord_context(stranger)
        self.assertIn("bulunamadı",manage_reminders("cancel",1))
        set_current_discord_context(self.ctx)
        self.assertIn("iptal",manage_reminders("cancel",1))
