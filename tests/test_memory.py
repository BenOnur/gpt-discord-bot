import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace as Obj
import unittest
import json
import tiktoken
from unittest.mock import patch, AsyncMock

from bot.memory import DiscordMemory


def msg(mid=1, uid=10, channel=20, guild=30, content="I like tea", bot=False):
    return Obj(id=mid, guild=Obj(id=guild) if guild else None,
               channel=Obj(id=channel), author=Obj(id=uid,display_name="same-name",bot=bot),
               content=content,created_at=datetime.now(timezone.utc),jump_url=f"https://discord.com/channels/{guild}/{channel}/{mid}")


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"memory":{"enabled":True,"guild_ids":[30],"data_dir":self.tmp.name}}
        self.memory = DiscordMemory(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingests_without_mention_persists_and_separates_users(self):
        self.memory.save(msg(1,10))
        self.memory.save(msg(2,11))
        reopened = DiscordMemory(self.cfg)
        self.assertEqual([r["author"] for r in reopened.pending()],[10,11])

    def test_guild_dm_and_bot_scope(self):
        for m in [msg(1,guild=None),msg(2,guild=31),msg(3,bot=True),msg(4,content="")]:
            self.memory.save(m)
        self.assertEqual(self.memory.pending(),[])

    def test_dedup_and_edit_reanalysis(self):
        self.memory.save(msg())
        with self.memory.db() as db:
            db.execute("UPDATE messages SET analyzed=1")
        self.memory.save(msg())
        self.assertEqual(self.memory.pending(),[])
        self.memory.save(msg(content="Actually coffee"))
        self.assertEqual(len(self.memory.pending()),1)

    def test_private_channel_not_recalled(self):
        allowed = Obj(id=20,permissions_for=lambda m:Obj(view_channel=True,read_message_history=True))
        denied = Obj(id=21,permissions_for=lambda m:Obj(view_channel=False,read_message_history=False))
        m = msg()
        m.guild = Obj(id=30,text_channels=[allowed,denied],threads=[])
        self.memory.save(msg(2,channel=21,content="secret"))
        calls=[]
        async def command(*args,**kw):
            calls.append(args)
            if args[1] == "search-notes":
                return {"results":[{"permalink":"guild-30/channel-21/hidden","content":"secret"},
                                   {"permalink":"guild-30/channel-20/public","content":"visible note"},
                                   {"permalink":"guild-31/channel-20/other-guild"}]}
            return {"content":"visible note"}
        self.memory.command=command
        text=asyncio.run(self.memory.recall(m))
        self.assertIn("visible note",text)
        self.assertNotIn("secret",text)
        reads=[c[2] for c in calls if c[1]=="read-note"]
        self.assertEqual(reads,[])

    def test_disabled_does_not_create_archive(self):
        path=Path(self.tmp.name)/"disabled"
        DiscordMemory({"memory":{"enabled":False,"data_dir":str(path)}})
        self.assertFalse(path.exists())

    def test_payload_budget_includes_metadata_and_preserves_full_archive(self):
        for mid in range(1,81):
            self.memory.save(msg(mid,content="Türkçe sohbet ve kaynak bilgisi. "*20))
        batch=self.memory.pending()
        payload="\n".join(json.dumps({k:r[k] for k in ("id","author","name","created","content","url")},ensure_ascii=False) for r in batch)
        self.assertLessEqual(len(tiktoken.get_encoding("o200k_base").encode(payload)),3550)
        self.assertLess(len(batch),80)
        self.memory.save(msg(0,content="😀"*12000))
        row=self.memory.pending()[0]
        self.assertLess(len(row["content"]),12000)
        with self.memory.db() as db:
            self.assertEqual(len(db.execute("SELECT content FROM messages WHERE id=0").fetchone()[0]),12000)

    def test_empty_historical_message_does_not_block_later_messages(self):
        async def history(**kw):
            yield msg(1,content="")
            yield msg(2,content="A real message after a sticker")
        channel=Obj(id=20,history=history,permissions_for=lambda m:Obj(view_channel=True,read_message_history=True))
        guild=Obj(id=30,me=Obj(id=99),text_channels=[channel],threads=[],channels=[])
        with patch("bot.memory.asyncio.sleep",new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.memory.backfill(Obj(guilds=[guild])))
        self.assertEqual([r["id"] for r in self.memory.pending()],[2])
        with self.memory.db() as db:
            self.assertEqual(db.execute("SELECT id FROM cursors WHERE channel=20").fetchone()[0],2)

    def test_live_memory_precedes_old_backlog_with_periodic_backfill(self):
        old = msg(1,content="old archive")
        from datetime import timedelta
        old.created_at = datetime.now(timezone.utc)-timedelta(days=365)
        self.memory.save(old)
        self.memory.save(msg(2,channel=21,content="today decision"))
        self.assertEqual(self.memory.pending()[0]["id"],2)
        with self.memory.db() as db:
            db.execute("INSERT INTO calls VALUES(?,4)",(datetime.now(timezone.utc).date().isoformat(),))
        self.assertEqual(self.memory.pending()[0]["id"],1)

    def test_deleted_message_removed_from_fts_and_pending(self):
        self.memory.save(msg(1,content="remove this secret"))
        asyncio.run(self.memory.forget_message(1))
        with self.memory.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM message_search WHERE message_search MATCH 'secret'").fetchone()[0],0)

    def test_image_only_message_keeps_metadata_without_copying_image(self):
        message=msg(content="")
        message.attachments=[Obj(id=5,filename="photo.png",content_type="image/png")]
        self.memory.save(message)
        with self.memory.db() as db:
            self.assertEqual(db.execute("SELECT filename FROM media").fetchone()[0],"photo.png")
            self.assertIn("photo.png",db.execute("SELECT content FROM messages").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
