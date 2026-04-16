import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sqlite3
import json
from datetime import datetime

# 1. Initialize Local SQLite Database (Replaces Supabase for Sessions/Chat ONLY)
def init_db():
	conn = sqlite3.connect("operix_studio.db")
	c = conn.cursor()
	c.execute('''CREATE TABLE IF NOT EXISTS sessions
				 (id TEXT PRIMARY KEY, filename TEXT, content TEXT, 
				  owner TEXT, collaborator TEXT, updated_at TEXT)''')
	c.execute('''CREATE TABLE IF NOT EXISTS messages
				 (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, 
				  sender TEXT, message TEXT, created_at TEXT)''')
	conn.commit()
	conn.close()

init_db()

app = FastAPI(title="OPERIX Server")

# 2. WebSocket Connection Manager
class ConnectionManager:
	def __init__(self):
		self.active_connections: dict[str, list[WebSocket]] = {}

	async def connect(self, websocket: WebSocket, session_id: str):
		await websocket.accept()
		if session_id not in self.active_connections:
			self.active_connections[session_id] = []
		self.active_connections[session_id].append(websocket)

	def disconnect(self, websocket: WebSocket, session_id: str):
		if session_id in self.active_connections:
			self.active_connections[session_id].remove(websocket)

	async def broadcast(self, message: str, session_id: str, exclude: WebSocket = None):
		if session_id in self.active_connections:
			for connection in self.active_connections[session_id]:
				if connection != exclude:
					await connection.send_text(message)

manager = ConnectionManager()

# 3. Routes
@app.get("/")
async def serve_ui():
	return FileResponse("index.html")

class SessionData(BaseModel):
	id: str
	filename: str
	owner: str
	collaborator: str

@app.post("/api/init_session")
async def init_session(data: SessionData):
	conn = sqlite3.connect("operix_studio.db")
	c = conn.cursor()
	c.execute("SELECT content FROM sessions WHERE id=?", (data.id,))
	row = c.fetchone()
	
	if row:
		# Session exists, fetch chat history too
		c.execute("SELECT sender, message FROM messages WHERE session_id=? ORDER BY id ASC", (data.id,))
		chats = [{"sender": r[0], "message": r[1]} for r in c.fetchall()]
		conn.close()
		return {"status": "exists", "content": row[0], "chats": chats}
	else:
		# Create new
		content = f"// Project: {data.filename}\n// Collaborators: {data.owner} & {data.collaborator}\n\n"
		c.execute("INSERT INTO sessions (id, filename, content, owner, collaborator, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
				  (data.id, data.filename, content, data.owner, data.collaborator, datetime.now().isoformat()))
		conn.commit()
		conn.close()
		return {"status": "created", "content": content, "chats": []}

@app.get("/api/history/{email}")
async def get_history(email: str):
	conn = sqlite3.connect("operix_studio.db")
	c = conn.cursor()
	c.execute("SELECT id, filename, owner, collaborator, updated_at FROM sessions WHERE owner=? OR collaborator=? ORDER BY updated_at DESC", (email, email))
	rows = [{"id": r[0], "filename": r[1], "owner": r[2], "collaborator": r[3], "updated_at": r[4]} for r in c.fetchall()]
	conn.close()
	return rows

@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
	conn = sqlite3.connect("operix_studio.db")
	c = conn.cursor()
	c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
	c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
	conn.commit()
	conn.close()
	return {"status": "deleted"}

@app.websocket("/ws/{session_id}/{email}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, email: str):
	await manager.connect(websocket, session_id)
	try:
		while True:
			data_str = await websocket.receive_text()
			data = json.loads(data_str)
			
			conn = sqlite3.connect("operix_studio.db")
			c = conn.cursor()
			
			if data["type"] == "code_update":
				c.execute("UPDATE sessions SET content=?, updated_at=? WHERE id=?", 
						  (data["content"], datetime.now().isoformat(), session_id))
				await manager.broadcast(data_str, session_id, exclude=websocket)
			
			elif data["type"] == "chat":
				c.execute("INSERT INTO messages (session_id, sender, message, created_at) VALUES (?, ?, ?, ?)",
						  (session_id, email, data["msg"], datetime.now().isoformat()))
				await manager.broadcast(data_str, session_id, exclude=websocket)
				
			conn.commit()
			conn.close()

	except WebSocketDisconnect:
		manager.disconnect(websocket, session_id)

if __name__ == "__main__":
	uvicorn.run("app:app", host="0.0.0.0", port=8000)