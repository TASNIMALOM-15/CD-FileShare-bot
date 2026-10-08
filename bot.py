import os, asyncio, time, secrets
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
from motor.motor_asyncio import AsyncIOMotorClient

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMINS = [int(x) for x in os.environ["ADMINS"].replace(",", " ").split()]
DB_CHANNEL = int(os.environ["DB_CHANNEL"])
MONGO_URI = os.environ["MONGO_URI"]
PORT = int(os.environ.get("PORT", "8080"))

DEFAULT = {
    "del_min": int(os.environ.get("DELETE_MINUTES", "15")),
    "protect": os.environ.get("PROTECT_CONTENT", "0") == "1",
    "caption": "", "fsub": [], "exp_h": 0, "max_uses": 0,
    "welcome": "স্বাগতম! ফাইল পেতে আপনাকে পাঠানো লিঙ্কে ক্লিক করুন।",
    "warn": "⚠️ সতর্কতা: {min} মিনিট পর এই ফাইলগুলো এবং এই মেসেজ অটো ডিলিট হয়ে যাবে।\n"
            "এখনই ফাইলগুলো Save করে নিন অথবা অন্য কোনো চ্যানেল/চ্যাটে Forward করে রাখুন।",
}
db = AsyncIOMotorClient(MONGO_URI)["linkbot"]
batches, jobs, users, bans = db.batches, db.jobs, db.users, db.bans
app = Client("linkbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
BOT_USERNAME = ""
pending, state, ask_pw, inv = {}, {}, {}, {}

MEDIA = (filters.document | filters.video | filters.audio | filters.photo |
         filters.voice | filters.animation | filters.video_note)
admin_f = filters.private & filters.user(ADMINS)
has_state = filters.create(lambda _, __, m: bool(m.from_user) and
                           (m.from_user.id in state or m.from_user.id in ask_pw))


async def cfg():
    return {**DEFAULT, **(await db.settings.find_one({"_id": "c"}) or {})}


async def setcfg(k, v):
    await db.settings.update_one({"_id": "c"}, {"$set": {k: v}}, upsert=True)


async def safe(fn, *a, **kw):
    while True:
        try:
            return await fn(*a, **kw)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)


# ---------------- সেটিংস প্যানেল ----------------
PROMPT = {
    "del_min": "⏱ কত মিনিট পর ফাইল ডিলিট হবে? সংখ্যা পাঠান (যেমন 15)",
    "caption": "📝 ফাইলের নিচে যে ক্যাপশন বসবে তা লিখুন।\nবন্ধ করতে লিখুন: clear",
    "fsub": "📢 Force Sub চ্যানেলের ID পাঠান (যেমন -100123456789)। বট ওই চ্যানেলে Admin থাকতে হবে।\n"
            "একই ID আবার পাঠালে চ্যানেলটি বাদ যাবে।\nবর্তমান: {fsub}",
    "welcome": "👋 নতুন Welcome মেসেজ লিখুন।",
    "warn": "⚠️ নতুন সতর্কতা মেসেজ লিখুন। সময় বসাতে {min} লিখুন।",
    "exp_h": "⏳ নতুন লিঙ্কের ডিফল্ট মেয়াদ কত ঘণ্টা? (0 = সীমাহীন)",
    "max_uses": "🔢 নতুন লিঙ্ক কতবার ব্যবহার করা যাবে? (0 = সীমাহীন)",
}


async def panel():
    c = await cfg()
    cap = "আছে" if c["caption"] else "নেই"
    kb = [
        [IKB(f"⏱ ডিলিট সময়: {c['del_min']} মিনিট", "s:del_min")],
        [IKB(f"🔒 Protect: {'ON' if c['protect'] else 'OFF'}", "s:protect")],
        [IKB(f"📝 ক্যাপশন: {cap}", "s:caption"), IKB(f"📢 Force Sub: {len(c['fsub'])}", "s:fsub")],
        [IKB("👋 Welcome", "s:welcome"), IKB("⚠️ সতর্কতা মেসেজ", "s:warn")],
        [IKB(f"⏳ ডিফল্ট মেয়াদ: {c['exp_h']}ঘ", "s:exp_h"), IKB(f"🔢 ডিফল্ট লিমিট: {c['max_uses']}", "s:max_uses")],
    ]
    return "⚙️ সেটিংস (বাটন চেপে বদলান)", IKM(kb)


async def lpanel(bid):
    d = await batches.find_one({"_id": bid})
    if not d:
        return None
    exp = d.get("exp")
    e = "সীমাহীন" if not exp else (f"{max(0, int((exp - time.time()) / 3600))} ঘণ্টা বাকি")
    mx = d.get("max") or "∞"
    t = (f"🔗 https://t.me/{BOT_USERNAME}?start={bid}\n\n📁 ফাইল: {len(d['ids'])}টি\n"
         f"👁 ব্যবহার: {d.get('uses', 0)}/{mx}\n⏳ মেয়াদ: {e}\n🔑 পাসওয়ার্ড: {'আছে' if d.get('pw') else 'নেই'}")
    kb = [[IKB("⏳ মেয়াদ", f"l:{bid}:exp"), IKB("🔢 লিমিট", f"l:{bid}:max")],
          [IKB("🔑 পাসওয়ার্ড", f"l:{bid}:pw"), IKB("🗑 বন্ধ করুন", f"l:{bid}:rev")]]
    return t, IKM(kb)


@app.on_callback_query(filters.user(ADMINS))
async def cb(c, q):
    p = q.data.split(":")
    uid = q.from_user.id
    if p[0] == "s":
        k = p[1]
        if k == "protect":
            await setcfg("protect", not (await cfg())["protect"])
            t, kb = await panel()
            try:
                await q.message.edit_text(t, reply_markup=kb)
            except Exception:
                pass
        else:
            state[uid] = ("cfg", k)
            await q.message.reply(PROMPT[k].replace("{fsub}", str((await cfg())["fsub"])) + "\n\n(বাতিল: /cancel)")
    elif p[0] == "l":
        bid, act = p[1], p[2]
        if act == "rev":
            await batches.delete_one({"_id": bid})
            await q.message.edit_text("🗑 লিঙ্কটি বন্ধ করা হয়েছে।")
        elif act == "open":
            r = await lpanel(bid)
            if r:
                await q.message.reply(r[0], reply_markup=r[1], disable_web_page_preview=True)
        else:
            state[uid] = ("l", bid, act)
            ps = {"exp": "⏳ কত ঘণ্টা মেয়াদ? (0 = সীমাহীন)", "max": "🔢 কতবার ব্যবহার করা যাবে? (0 = সীমাহীন)",
                  "pw": "🔑 পাসওয়ার্ড লিখুন (বন্ধ করতে: clear)"}
            await q.message.reply(ps[act] + "\n\n(বাতিল: /cancel)")
    await q.answer()


@app.on_message(has_state & filters.private & filters.text & ~filters.command(["start", "cancel", "done"]))
async def textin(c, m):
    uid = m.from_user.id
    txt = m.text.strip()
    if uid in state:
        st = state.pop(uid)
        if st[0] == "cfg":
            k = st[1]
            if k in ("del_min", "exp_h", "max_uses"):
                if not txt.isdigit() or (k == "del_min" and int(txt) < 1):
                    state[uid] = st
                    return await m.reply("❌ সঠিক সংখ্যা লিখুন।")
                await setcfg(k, int(txt))
            elif k == "fsub":
                try:
                    ch = int(txt)
                except ValueError:
                    state[uid] = st
                    return await m.reply("❌ সঠিক ID লিখুন (যেমন -100123...)।")
                lst = (await cfg())["fsub"]
                lst = [x for x in lst if x != ch] if ch in lst else lst + [ch]
                inv.pop(ch, None)
                await setcfg("fsub", lst)
            else:
                await setcfg(k, "" if (k == "caption" and txt.lower() == "clear") else m.text)
            t, kb = await panel()
            return await m.reply("✅ সেভ হয়েছে।\n\n" + t, reply_markup=kb)
        if st[0] == "l":
            _, bid, act = st
            if act in ("exp", "max"):
                if not txt.isdigit():
                    state[uid] = st
                    return await m.reply("❌ সংখ্যা লিখুন।")
                n = int(txt)
                if act == "exp":
                    upd = {"exp": time.time() + n * 3600} if n else {"exp": None}
                else:
                    upd = {"max": n}
            else:
                upd = {"pw": None if txt.lower() == "clear" else txt}
            await batches.update_one({"_id": bid}, {"$set": upd})
            r = await lpanel(bid)
            return await m.reply("✅ সেভ হয়েছে।\n\n" + r[0], reply_markup=r[1], disable_web_page_preview=True)
    if uid in ask_pw:
        bid = ask_pw.pop(uid)
        d = await batches.find_one({"_id": bid})
        if d and txt == d.get("pw"):
            return await send_batch(c, m, bid, ok=True)
        ask_pw[uid] = bid
        await m.reply("❌ ভুল পাসওয়ার্ড। আবার চেষ্টা করুন।")


# ---------------- ইউজার সাইড ----------------
async def invite(ch):
    if ch not in inv:
        chat = await app.get_chat(ch)
        inv[ch] = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else
                                       await app.export_chat_invite_link(ch))
    return inv[ch]


async def not_joined(uid, chs):
    out = []
    for ch in chs:
        try:
            mem = await app.get_chat_member(ch, uid)
            if mem.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
                out.append(ch)
        except UserNotParticipant:
            out.append(ch)
        except Exception:
            pass
    return out


@app.on_message(filters.command("start") & filters.private)
async def start(c, m):
    uid = m.from_user.id
    await users.update_one({"_id": uid}, {"$set": {"t": time.time()}}, upsert=True)
    if await bans.find_one({"_id": uid}):
        return await m.reply("🚫 আপনাকে ব্লক করা হয়েছে।")
    if len(m.command) > 1:
        return await send_batch(c, m, m.command[1])
    if uid in ADMINS:
        return await m.reply(
            "অ্যাডমিন প্যানেল:\n১) আমাকে ফাইল পাঠান\n২) /done দিন - লিঙ্ক পাবেন\n\n"
            "/settings - সব সেটিংস\n/links - লিঙ্ক তালিকা\n/broadcast - (মেসেজে রিপ্লাই দিয়ে)\n"
            "/ban id  /unban id\n/stats  /cancel")
    await m.reply((await cfg())["welcome"])


async def send_batch(c, m, bid, ok=False):
    uid = m.chat.id
    d = await batches.find_one({"_id": bid})
    if not d:
        return await m.reply("❌ এই লিঙ্কটি ঠিক নয় অথবা বন্ধ করা হয়েছে।")
    if d.get("exp") and time.time() > d["exp"]:
        return await m.reply("⌛ এই লিঙ্কের মেয়াদ শেষ।")
    if d.get("max") and d.get("uses", 0) >= d["max"]:
        return await m.reply("🚫 এই লিঙ্কের ব্যবহার সীমা শেষ।")
    cf = await cfg()
    nj = await not_joined(uid, cf["fsub"])
    if nj:
        kb = []
        for ch in nj:
            try:
                kb.append([IKB("📢 চ্যানেল Join করুন", url=await invite(ch))])
            except Exception:
                pass
        kb.append([IKB("🔄 আবার চেষ্টা করুন", url=f"https://t.me/{BOT_USERNAME}?start={bid}")])
        return await m.reply("ফাইল পেতে আগে নিচের চ্যানেলে Join করুন, তারপর আবার চেষ্টা করুন।", reply_markup=IKM(kb))
    if d.get("pw") and not ok:
        ask_pw[uid] = bid
        return await m.reply("🔑 এই লিঙ্কের পাসওয়ার্ড লিখুন:")
    sent = []
    for mid in d["ids"]:
        try:
            r = await safe(c.copy_message, uid, DB_CHANNEL, mid,
                           caption=cf["caption"] or None, protect_content=cf["protect"])
        except Exception:
            try:
                r = await safe(c.copy_message, uid, DB_CHANNEL, mid, protect_content=cf["protect"])
            except Exception:
                continue
        sent.append(r.id)
    if not sent:
        return await m.reply("❌ ফাইল পাঠানো যায়নি।")
    note = await m.reply(cf["warn"].replace("{min}", str(cf["del_min"])))
    sent.append(note.id)
    await jobs.insert_one({"chat": uid, "ids": sent, "at": time.time() + cf["del_min"] * 60})
    await batches.update_one({"_id": bid}, {"$inc": {"uses": 1}})


# ---------------- অ্যাডমিন সাইড ----------------
@app.on_message(admin_f & MEDIA)
async def collect(c, m):
    r = await safe(m.copy, DB_CHANNEL)
    pending.setdefault(m.from_user.id, []).append(r.id)
    await m.reply(f"✅ যোগ হয়েছে। মোট: {len(pending[m.from_user.id])}টি। শেষ হলে /done দিন।", quote=True)


@app.on_message(admin_f & filters.command("done"))
async def done(c, m):
    ids = pending.pop(m.from_user.id, [])
    if not ids:
        return await m.reply("আগে ফাইল পাঠান।")
    cf = await cfg()
    bid = secrets.token_urlsafe(6)
    await batches.insert_one({"_id": bid, "ids": ids, "t": time.time(), "uses": 0,
                              "max": cf["max_uses"],
                              "exp": time.time() + cf["exp_h"] * 3600 if cf["exp_h"] else None})
    r = await lpanel(bid)
    await m.reply(r[0], reply_markup=r[1], disable_web_page_preview=True)


@app.on_message(admin_f & filters.command("settings"))
async def settings(c, m):
    t, kb = await panel()
    await m.reply(t, reply_markup=kb)


@app.on_message(admin_f & filters.command("links"))
async def links(c, m):
    kb, n = [], 0
    async for d in batches.find({}).sort("t", -1).limit(20):
        n += 1
        kb.append([IKB(f"{d['_id']} | {len(d['ids'])} ফাইল | 👁 {d.get('uses', 0)}", f"l:{d['_id']}:open")])
    if not n:
        return await m.reply("কোনো লিঙ্ক নেই।")
    await m.reply("🔗 আপনার লিঙ্ক (চেপে ম্যানেজ করুন):", reply_markup=IKM(kb))


@app.on_message(admin_f & filters.command("cancel"))
async def cancel(c, m):
    pending.pop(m.from_user.id, None)
    state.pop(m.from_user.id, None)
    await m.reply("বাতিল করা হয়েছে।")


@app.on_message(admin_f & filters.command(["ban", "unban"]))
async def ban(c, m):
    if len(m.command) < 2 or not m.command[1].isdigit():
        return await m.reply("ব্যবহার: /ban 123456789")
    uid = int(m.command[1])
    if m.command[0] == "ban":
        await bans.update_one({"_id": uid}, {"$set": {"t": time.time()}}, upsert=True)
        await m.reply("🚫 ব্লক করা হয়েছে।")
    else:
        await bans.delete_one({"_id": uid})
        await m.reply("✅ আনব্লক করা হয়েছে।")


@app.on_message(admin_f & filters.command("broadcast"))
async def broadcast(c, m):
    r = m.reply_to_message
    if not r:
        return await m.reply("যে মেসেজ পাঠাবেন তাতে রিপ্লাই দিয়ে /broadcast লিখুন।")
    ok = bad = 0
    async for u in users.find({}):
        try:
            await safe(r.copy, u["_id"])
            ok += 1
        except Exception:
            bad += 1
    await m.reply(f"📣 পাঠানো হয়েছে: {ok}\nব্যর্থ: {bad}")


@app.on_message(admin_f & filters.command("stats"))
async def stats(c, m):
    await m.reply(f"👥 ইউজার: {await users.count_documents({})}\n🔗 লিঙ্ক: {await batches.count_documents({})}\n"
                  f"🚫 ব্লক: {await bans.count_documents({})}")


# ---------------- অটো ডিলিট + সার্ভার ----------------
async def cleaner():
    while True:
        try:
            async for j in jobs.find({"at": {"$lte": time.time()}}):
                try:
                    await safe(app.delete_messages, j["chat"], j["ids"])
                except Exception:
                    pass
                await jobs.delete_one({"_id": j["_id"]})
        except Exception as e:
            print("cleaner error:", e)
        await asyncio.sleep(20)


async def main():
    global BOT_USERNAME
    await app.start()
    BOT_USERNAME = (await app.get_me()).username
    w = web.Application()
    w.add_routes([web.get("/", lambda r: web.Response(text="OK"))])
    runner = web.AppRunner(w)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    asyncio.create_task(cleaner())
    print("Bot started:", BOT_USERNAME)
    await idle()
    await app.stop()


app.run(main())
