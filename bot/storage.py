"""SQLite connections whose transaction context also closes the handle."""
import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()
