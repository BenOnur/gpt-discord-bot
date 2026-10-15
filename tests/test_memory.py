import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace as Obj
import unittest

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


if __name__ == "__main__":
    unittest.main()
