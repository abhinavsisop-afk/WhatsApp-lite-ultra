# whatsapplite_prod_full.py
"""
WhatsApp-Lite PROD — single-file Flask + Flask-SocketIO + SQLite (SQLAlchemy)
Complete merged file: server + embedded client HTML.
Dev NOTE: demo-quality. Use HTTPS, proper auth, file scanning, and a real DB for production.
Sample local file path (developer-provided) is embedded below.
"""
import os, secrets, time
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_from_directory, url_for
from flask_socketio import SocketIO, emit, join_room
from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime,
                        Boolean, JSON, ForeignKey)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ---------- Config ----------
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8000))
APP_ROOT = Path(__file__).parent.resolve()
UPLOAD_FOLDER = APP_ROOT / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
STATIC_FOLDER = APP_ROOT / "static"
STATIC_FOLDER.mkdir(exist_ok=True)

# developer-provided sample file path (from your session)
SAMPLE_LOCAL_PATH = r"/mnt/data/aa50ff07-ad17-4603-8fd4-ff744d54cd9d.jpg"

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{APP_ROOT/'whatsapplite.db'}")
SECRET_KEY = os.environ.get("SECRET_KEY", "prod-demo-secret")

# ---------- Flask + SocketIO ----------
app = Flask(__name__, static_folder=str(STATIC_FOLDER))
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", ping_interval=25, ping_timeout=60)

# ---------- SQLAlchemy ----------
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    avatar = Column(String(256), default="")
    created = Column(DateTime, default=datetime.utcnow)
    devices = relationship("Device", back_populates="user")

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    token = Column(String(128), unique=True, nullable=False)
    device_id = Column(String(64))
    user_id = Column(Integer, ForeignKey("users.id"))
    created = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="devices")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    msg_id = Column(String(64), unique=True, nullable=False)
    room = Column(String(64), index=True)
    author = Column(String(64))
    text = Column(Text)
    mtype = Column(String(32))
    file = Column(String(512))
    ts = Column(DateTime, default=datetime.utcnow)
    edited = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)
    reactions = Column(JSON, default={})
    pinned = Column(Boolean, default=False)
    read_by = Column(JSON, default=[])

Base.metadata.create_all(engine)

# ---------- In-memory runtime ----------
online_users = {}    # username -> set(sids)
sid_to_user = {}     # sid -> username

# ---------- Helpers ----------
def now_ts():
    return datetime.utcnow().strftime("%H:%M:%S %d-%m")

def gen_token():
    return secrets.token_hex(24)

def mk_msg_db(sess, author, text="", mtype="text", file_url=None, room="main"):
    msg = Message(
        msg_id = secrets.token_hex(10),
        room = room,
        author = author,
        text = text,
        mtype = mtype,
        file = file_url,
        ts = datetime.utcnow(),
        edited=False,
        deleted=False,
        reactions={},
        pinned=False,
        read_by=[]
    )
    sess.add(msg); sess.commit(); sess.refresh(msg)
    return msg

def message_to_dict(m):
    return {
        "id": m.msg_id,
        "room": m.room,
        "name": m.author,
        "msg": m.text,
        "type": m.mtype,
        "file": m.file,
        "ts": m.ts.strftime("%H:%M:%S %d-%m"),
        "edited": m.edited,
        "deleted": m.deleted,
        "reactions": m.reactions or {},
        "pinned": bool(m.pinned),
        "read_by": m.read_by or []
    }

# ---------- Embedded Client HTML (ULTRA UI) ----------
HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>WhatsApp-Lite ULTRA</title>
  <style>
    :root{--me:#DCF8C6;--you:#111;--bg:#0f0f12}
    body{background:var(--bg);color:#ddd;font-family:Inter,Arial;margin:0;padding:18px}
    #wrap{max-width:1100px;margin:0 auto;display:flex;gap:16px}
    .col{background:#101114;padding:14px;border-radius:10px}
    #sidebar{width:280px}
    #main{flex:1;display:flex;flex-direction:column}
    #chatbox{flex:1;height:520px;overflow:auto;background:#070708;padding:12px;border-radius:8px}
    .msg{display:flex;margin:8px 0;align-items:flex-end}
    .bubble{max-width:70%;padding:10px;border-radius:12px;line-height:1.2}
    .me{margin-left:auto;flex-direction:row-reverse}
    .me .bubble{background:var(--me);color:#000}
    .you .bubble{background:#222;color:#eee}
    .meta{font-size:11px;color:#999;margin-top:6px}
    .controls{display:flex;gap:8px;margin-top:8px}
    input,button,select,textarea{padding:8px;border-radius:6px;border:1px solid #222;background:#0d0d0f;color:#ddd}
    .rooms{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
    .room-btn{padding:6px 10px;background:#141416;border-radius:8px;cursor:pointer}
    #onlineList{font-size:13px;margin-top:8px;color:#bcd}
    #typing{color:#9c9;margin-top:6px;height:18px}
    .small{font-size:12px;color:#bbb}
    .actions{display:flex;gap:6px;margin-left:6px}
    .file-thumb{max-width:220px;border-radius:8px;display:block;margin-top:6px}
  </style>
</head>
<body>
<div id="wrap">
  <div id="sidebar" class="col">
    <div>
      <input id="user" placeholder="username (no password)">
      <input id="device" placeholder="device id (web)" value="web" style="width:120px">
      <button id="loginBtn">Get Token / Login</button>
      <button id="logoutBtn">Logout</button>
    </div>
    <div class="small" style="margin-top:6px">Token stored in localStorage</div>
    <hr>
    <div id="onlineList"><b>Online:</b><div id="onlineUsers"></div></div>
    <hr>
    <div>
      <label class="small">Rooms</label>
      <div class="rooms">
        <div class="room-btn" data-room="main">main</div>
        <div class="room-btn" data-room="school">school</div>
        <div class="room-btn" data-room="games">games</div>
      </div>
      <div style="margin-top:8px">
        <input id="roomInput" placeholder="room name">
        <button id="joinBtn">Join</button>
      </div>
    </div>
    <hr>
    <div class="small">Search messages</div>
    <input id="searchInput" placeholder="search text">
    <button id="searchBtn">Search</button>
    <div id="searchResults" class="small"></div>
    <hr>
    <div class="small">Pinned</div>
    <div id="pinnedList" class="small"></div>
    <hr>
    <div class="small">Tip: Files saved in /uploads on server</div>
    <div style="margin-top:8px"><small>dev sample file: <a href="file://{{ sample_local }}" target="_blank">sample.jpg</a></small></div>
  </div>

  <div id="main" class="col">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <h3 style="margin:0">WhatsApp-Lite ULTRA — <span id="roomLabel">main</span></h3>
      <div class="small">me: <span id="meName">guest</span></div>
    </div>
    <div id="chatbox"></div>
    <div id="typing" class="small"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <input id="msgInput" placeholder="Type message..." style="flex:1">
      <input type="file" id="fileInput" accept="image/*,audio/*,video/*,application/*">
      <button id="recordBtn">🎙️</button>
      <button id="sendBtn">Send</button>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io({transports:["websocket","polling"]});
let token = localStorage.getItem('ultra_token');
let me = localStorage.getItem('ultra_user') || 'guest';
let deviceId = localStorage.getItem('ultra_device') || 'web';
let currentRoom = 'main';
let mediaRecorder=null, mediaStream=null, chunks=[];

document.getElementById('meName').textContent = me;
document.getElementById('roomLabel').textContent = currentRoom;
document.getElementById('user').value = me;
document.getElementById('device').value = deviceId;

function addMessage(o){
  const cb = document.getElementById('chatbox');
  const m = document.createElement('div');
  m.className = 'msg ' + (o.name === me ? 'me' : 'you');
  const bubble = document.createElement('div'); bubble.className='bubble';
  const head = document.createElement('div'); head.innerHTML = '<b>'+escapeHtml(o.name)+'</b>';
  const body = document.createElement('div'); body.innerHTML = (o.deleted? '<i>(deleted)</i>' : (o.msg? escapeHtml(o.msg): ''));
  bubble.appendChild(head);
  bubble.appendChild(body);
  if(o.file){
    if(o.type && o.type.startsWith('audio')){
      const a=document.createElement('audio'); a.controls=true; a.src=o.file; bubble.appendChild(a);
    } else if(o.type && o.type.startsWith('image')){
      const im=document.createElement('img'); im.src=o.file; im.className='file-thumb'; bubble.appendChild(im);
    } else {
      const a=document.createElement('a'); a.href=o.file; a.textContent='Download file'; a.target='_blank'; bubble.appendChild(a);
    }
  }
  const meta = document.createElement('div'); meta.className='meta';
  let ticks = '';
  if(o.read_by && o.read_by.length>0 && o.name === me){
    ticks = ' ✔✔';
  } else if(o.name === me){
    ticks = ' ✔';
  }
  meta.textContent = o.ts + ticks;
  bubble.appendChild(meta);

  const reactDiv = document.createElement('div'); reactDiv.className='small';
  if(o.reactions){
    for(const em in o.reactions){
      const who = o.reactions[em];
      const btn = document.createElement('button'); btn.textContent = em + ' ' + who.length;
      btn.onclick = ()=> { socket.emit('react', {room: o.room, id: o.id, emoji: em, name: me}); };
      reactDiv.appendChild(btn);
    }
  }
  const addReact = document.createElement('button'); addReact.textContent='😊';
  addReact.onclick = ()=>{ const em=prompt('emoji (simple char)'); if(em) socket.emit('react',{room:o.room,id:o.id,emoji:em,name:me}); };
  reactDiv.appendChild(addReact);

  bubble.appendChild(reactDiv);

  if(o.name === me){
    const actions = document.createElement('span'); actions.className='actions';
    const del = document.createElement('button'); del.textContent='Delete'; del.onclick = ()=> socket.emit('delete_msg',{room: o.room, id:o.id});
    const edit = document.createElement('button'); edit.textContent='Edit'; edit.onclick = ()=> { const t = prompt('edit text', o.msg); if(t!==null) socket.emit('edit_msg',{room:o.room,id:o.id, msg:t}); };
    const pin = document.createElement('button'); pin.textContent = o.pinned? 'Unpin' : 'Pin'; pin.onclick = ()=> socket.emit('pin_msg',{room:o.room,id:o.id, pin:!o.pinned});
    actions.appendChild(edit); actions.appendChild(del); actions.appendChild(pin);
    bubble.appendChild(actions);
  }

  m.appendChild(bubble);
  cb.appendChild(m);
  cb.scrollTop = cb.scrollHeight;
}

function escapeHtml(s){ if(!s) return ''; return s.replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function login(){
  const user = document.getElementById('user').value.trim();
  const dev = document.getElementById('device').value.trim() || 'web';
  if(!user) return alert('enter username');
  const fd = new FormData(); fd.append('user', user); fd.append('device', dev);
  const res = await fetch('/login', {method:'POST', body:fd});
  const j = await res.json();
  if(!j.ok) return alert('login failed: '+(j.err||''));
  token = j.token; me = j.user; deviceId = dev;
  localStorage.setItem('ultra_token', token); localStorage.setItem('ultra_user', me); localStorage.setItem('ultra_device', deviceId);
  document.getElementById('meName').textContent = me;
  socket.emit('auth', {token: token});
  alert('logged in as ' + me);
}

async function logout(){
  if(!token) return alert('no token');
  const fd = new FormData(); fd.append('token', token);
  await fetch('/logout', {method:'POST', body:fd});
  localStorage.removeItem('ultra_token'); localStorage.removeItem('ultra_user');
  token = null; me='guest'; document.getElementById('meName').textContent = me;
  alert('logged out');
}

async function sendMsg(){
  const txt = document.getElementById('msgInput').value.trim();
  const file = document.getElementById('fileInput').files[0];
  if(!txt && !file) return;
  if(file){
    const fd = new FormData(); fd.append('file', file); fd.append('room', currentRoom); fd.append('name', me);
    const r = await fetch('/upload', {method:'POST', body:fd}); const j = await r.json();
    if(!j.ok) return alert('upload failed');
  }
  if(txt){
    socket.emit('msg', {room: currentRoom, name: me, msg: txt});
    document.getElementById('msgInput').value='';
  }
}

document.getElementById('recordBtn').onclick = async ()=>{
  if(!mediaRecorder){
    try{
      mediaStream = await navigator.mediaDevices.getUserMedia({audio:true});
      mediaRecorder = new MediaRecorder(mediaStream);
      chunks = [];
      mediaRecorder.ondataavailable = e => chunks.push(e.data);
      mediaRecorder.onstop = async ()=>{
        const blob = new Blob(chunks, {type:'audio/webm'});
        const fd = new FormData(); fd.append('file', blob, 'voice.webm'); fd.append('room', currentRoom); fd.append('name', me);
        const r = await fetch('/upload_voice', {method:'POST', body:fd}); const j = await r.json();
        if(!j.ok) alert('voice upload failed');
      };
      mediaRecorder.start();
      document.getElementById('recordBtn').textContent='⏹️ Stop';
    }catch(e){ alert('mic permission needed'); }
  } else {
    mediaRecorder.stop();
    mediaRecorder = null;
    mediaStream.getTracks().forEach(t=>t.stop());
    mediaStream = null;
    document.getElementById('recordBtn').textContent='🎙️';
  }
};

document.getElementById('loginBtn').onclick = login;
document.getElementById('logoutBtn').onclick = logout;
document.getElementById('sendBtn').onclick = sendMsg;
document.getElementById('joinBtn').onclick = ()=>{ const r=document.getElementById('roomInput').value.trim(); if(r) joinRoom(r); };
document.querySelectorAll('.room-btn').forEach(b=> b.onclick = ()=> joinRoom(b.dataset.room));
document.getElementById('searchBtn').onclick = async ()=>{
  const q = document.getElementById('searchInput').value.trim();
  if(!q) return;
  const res = await fetch('/search?room='+encodeURIComponent(currentRoom)+'&q='+encodeURIComponent(q));
  const j = await res.json();
  const out = document.getElementById('searchResults'); out.innerHTML = j.results.map(m=> `<div><b>${escapeHtml(m.name)}</b>: ${escapeHtml(m.msg)} <span class="small">${m.ts}</span></div>`).join('');
};

function joinRoom(r){
  currentRoom = r;
  document.getElementById('roomLabel').textContent = r;
  document.getElementById('chatbox').innerHTML = '';
  socket.emit('join', {room: r});
}

socket.on('connect', ()=> {
  token = localStorage.getItem('ultra_token');
  if(token) socket.emit('auth', {token: token});
  socket.emit('join', {room: currentRoom});
});

socket.on('history', data => {
  document.getElementById('chatbox').innerHTML = '';
  data.forEach(addMessage);
  if(token) socket.emit('read_all', {room: currentRoom, name: me});
});

socket.on('msg', o => {
  addMessage(o);
  try { new Audio('/static/ping.mp3').play().catch(()=>{}); } catch(e){}
  socket.emit('delivered', {id: o.id, room: currentRoom});
  if(document.hasFocus()) socket.emit('read', {id: o.id, room: currentRoom, name: me});
});

socket.on('online_update', list => {
  document.getElementById('onlineUsers').innerHTML = list.map(x=> '<div>'+escapeHtml(x)+'</div>').join('');
});

socket.on('presence', d => { console.log('presence', d); });
socket.on('typing', d => { if(d.name && d.name!==me) { document.getElementById('typing').textContent = d.name + ' is typing...'; setTimeout(()=> document.getElementById('typing').textContent = '', 1500); } });
socket.on('read_update', msgs => { socket.emit('join', {room: currentRoom}); });
socket.on('msg_update', m => { socket.emit('join', {room: currentRoom}); });
socket.on('delivered', d => { /* UI update hook */ });
socket.on('read', d => { /* UI update hook */ });

</script>
</body>
</html>
"""

# ---------- Routes ----------
@app.route("/")
def index():
    return render_template_string(HTML, sample_local=SAMPLE_LOCAL_PATH)

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/login", methods=["POST"])
def login():
    user = (request.form.get("user") or "").strip()
    device = (request.form.get("device") or "web").strip()
    if not user:
        return jsonify(ok=False, err="no-user"), 400
    sess = SessionLocal()
    u = sess.query(User).filter_by(username=user).first()
    if not u:
        u = User(username=user); sess.add(u); sess.commit(); sess.refresh(u)
    token = gen_token()
    d = Device(token=token, device_id=device, user=u)
    sess.add(d); sess.commit()
    return jsonify(ok=True, token=token, user=u.username, avatar=u.avatar)

@app.route("/logout", methods=["POST"])
def logout():
    token = request.form.get("token")
    if not token:
        return jsonify(ok=False, err="no-token"), 400
    sess = SessionLocal()
    d = sess.query(Device).filter_by(token=token).first()
    if d:
        sess.delete(d); sess.commit()
    return jsonify(ok=True)

@app.route("/upload", methods=["POST"])
def upload():
    if 'file' not in request.files:
        return jsonify(ok=False, err="no-file"), 400
    f = request.files['file']
    room = request.form.get('room', 'main')
    name = request.form.get('name', 'Anonymous')
    ext = Path(f.filename).suffix if f.filename else ''
    fname = f"{int(time.time())}_{secrets.token_hex(8)}{ext or '.bin'}"
    dest = Path(app.config['UPLOAD_FOLDER']) / fname
    f.save(str(dest))
    url = url_for('uploaded_file', filename=fname, _external=True)
    sess = SessionLocal()
    mim = f.content_type or ""
    typ = 'file'
    if mim.startswith('image'): typ='image'
    if mim.startswith('audio'): typ='audio'
    if mim.startswith('video'): typ='video'
    msg = mk_msg_db(sess, author=name, text="(file)", mtype=typ, file_url=url, room=room)
    socketio.emit('msg', message_to_dict(msg), to=room)
    return jsonify(ok=True, file=url)

@app.route("/upload_voice", methods=["POST"])
def upload_voice():
    if 'file' not in request.files:
        return jsonify(ok=False, err="no-file"), 400
    f = request.files['file']
    room = request.form.get('room', 'main')
    name = request.form.get('name', 'Anonymous')
    ext = Path(f.filename).suffix or '.webm'
    fname = f"{int(time.time())}_{secrets.token_hex(8)}{ext}"
    dest = Path(app.config['UPLOAD_FOLDER']) / fname
    f.save(str(dest))
    url = url_for('uploaded_file', filename=fname, _external=True)
    sess = SessionLocal()
    msg = mk_msg_db(sess, author=name, text="(voice)", mtype="audio", file_url=url, room=room)
    socketio.emit('msg', message_to_dict(msg), to=room)
    return jsonify(ok=True, file=url)

@app.route("/search")
def search():
    room = request.args.get('room', 'main')
    q = (request.args.get('q') or '').lower().strip()
    if not q:
        return jsonify(results=[])
    sess = SessionLocal()
    rows = sess.query(Message).filter(Message.room==room, Message.text.ilike(f"%{q}%")).limit(200).all()
    out = [{"id":r.msg_id,"name":r.author,"msg":r.text,"ts":r.ts.strftime("%H:%M:%S %d-%m")} for r in rows]
    return jsonify(results=out)

# ---------- Socket handlers ----------
@socketio.on('connect')
def on_connect():
    print("connect", request.sid)

@socketio.on('auth')
def on_auth(data):
    token = data.get('token')
    if not token:
        return
    sess = SessionLocal()
    d = sess.query(Device).filter_by(token=token).first()
    if not d:
        return
    user = d.user.username
    sid_to_user[request.sid] = user
    online_users.setdefault(user, set()).add(request.sid)
    join_room(f"user:{user}")
    socketio.emit('presence', {"user":user, "online":True})
    socketio.emit('online_update', list(online_users.keys()))
    print(f"auth: {user} connected sid={request.sid}")

@socketio.on('join')
def on_join(data):
    room = data.get('room','main')
    join_room(room)
    sess = SessionLocal()
    rows = sess.query(Message).filter_by(room=room).order_by(Message.ts.asc()).limit(500).all()
    emit('history', [message_to_dict(r) for r in rows])

@socketio.on('msg')
def on_msg(data):
    room = data.get('room','main'); name = data.get('name','Anon'); text = data.get('msg','')
    sess = SessionLocal()
    msg = mk_msg_db(sess, author=name, text=text, mtype='text', file_url=None, room=room)
    socketio.emit('msg', message_to_dict(msg), to=room)
    emit('sent', {'id': msg.msg_id})

@socketio.on('typing')
def on_typing(data):
    room = data.get('room','main'); name = data.get('name','Anon')
    emit('typing', {'room':room, 'name':name}, to=room, include_self=False)

@socketio.on('delivered')
def on_delivered(d):
    socketio.emit('delivered', {'id':d.get('id')}, to=d.get('room'))

@socketio.on('read')
def on_read(d):
    msg_id = d.get('id'); room = d.get('room'); name = d.get('name')
    if not msg_id or not room or not name: return
    sess = SessionLocal()
    m = sess.query(Message).filter_by(msg_id=msg_id).first()
    if m:
        arr = m.read_by or []
        if name and name not in arr:
            arr.append(name); m.read_by = arr; sess.commit()
    socketio.emit('read', {'id': msg_id, 'name': name}, to=room)

@socketio.on('read_all')
def on_read_all(d):
    room = d.get('room'); name = d.get('name')
    changed = False
    sess = SessionLocal()
    for m in sess.query(Message).filter_by(room=room).all():
        arr = m.read_by or []
        if name and name not in arr:
            arr.append(name); m.read_by = arr; changed=True
    if changed:
        socketio.emit('read_update', [message_to_dict(r) for r in sess.query(Message).filter_by(room=room).all()], to=room)

@socketio.on('delete_msg')
def on_delete(d):
    room = d.get('room'); msg_id = d.get('id')
    sess = SessionLocal()
    m = sess.query(Message).filter_by(msg_id=msg_id).first()
    if m:
        m.deleted = True; m.text="(message deleted)"; sess.commit()
        socketio.emit('msg_update', message_to_dict(m), to=room)

@socketio.on('edit_msg')
def on_edit(d):
    room = d.get('room'); msg_id = d.get('id'); new_text = d.get('msg')
    sess = SessionLocal()
    m = sess.query(Message).filter_by(msg_id=msg_id).first()
    if m:
        m.text = new_text; m.edited = True; m.ts = datetime.utcnow(); sess.commit()
        socketio.emit('msg_update', message_to_dict(m), to=room)

@socketio.on('react')
def on_react(d):
    room = d.get('room'); msg_id = d.get('id'); emoji = d.get('emoji'); name = d.get('name')
    if not emoji or not name: return
    sess = SessionLocal()
    m = sess.query(Message).filter_by(msg_id=msg_id).first()
    if m:
        rx = m.reactions or {}
        arr = rx.get(emoji, [])
        if name in arr:
            arr.remove(name)
        else:
            arr.append(name)
        rx[emoji] = arr
        m.reactions = rx; sess.commit()
        socketio.emit('msg_update', message_to_dict(m), to=room)

@socketio.on('pin_msg')
def on_pin(d):
    room = d.get('room'); msg_id = d.get('id'); pin = bool(d.get('pin'))
    sess = SessionLocal()
    m = sess.query(Message).filter_by(msg_id=msg_id).first()
    if m:
        m.pinned = pin; sess.commit()
        socketio.emit('msg_update', message_to_dict(m), to=room)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    user = sid_to_user.pop(sid, None)
    if user:
        s = online_users.get(user)
        if s:
            s.discard(sid)
            if not s:
                online_users.pop(user, None)
                socketio.emit('presence', {'user': user, 'online': False})
        socketio.emit('online_update', list(online_users.keys()))
    print("disconnect", sid)

# ---------- Seed demo ----------
def seed_demo():
    sess = SessionLocal()
    if sess.query(Message).count() == 0:
        mk_msg_db(sess, "System", "Welcome to WhatsApp-Lite PROD (persistent demo)!", mtype="text", room="main")
        mk_msg_db(sess, "Alice", "Say hi — persistence enabled.", mtype="text", room="main")
seed_demo()

# ---------- Run ----------
if __name__ == "__main__":
    print(f"WhatsApp-Lite PROD starting on http://{HOST}:{PORT}")
    # eventlet recommended: pip install eventlet
    socketio.run(app, host=HOST, port=PORT, debug=False)
