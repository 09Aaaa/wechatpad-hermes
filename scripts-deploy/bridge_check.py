import sqlite3, sys, time
db = '/mnt/user/appdata/wechatpad-hermes/data/wechatpad-hermes.sqlite3'
con = sqlite3.connect(db)
lp = con.execute("select value from runtime_state where key='last_poll_message_count'").fetchone()
lpt = con.execute("select value from runtime_state where key='last_poll_at'").fetchone()
lr = con.execute("select created_at from replies order by rowid desc limit 1").fetchone()
lm = con.execute("select create_time from messages order by rowid desc limit 1").fetchone()
print(str(lp[0] if lp else '-1') + '|' + str(lpt[0] if lpt else '0') + '|' + str(lr[0] if lr else 'none') + '|' + str(lm[0] if lm else 'none'))
con.close()