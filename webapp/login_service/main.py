# auth/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os, uvicorn

app = FastAPI(title="EnergyGuard Auth")

# === Config ===
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "eg-admin-token")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")
PORT = int(os.environ.get("PORT", "7000"))

# === CORS (webapp su 3000/5173) ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(req: LoginReq):
    if req.username == ADMIN_USER and req.password == ADMIN_PASS:
        # Il campo si chiama ESATTAMENTE 'token'
        return {"token": ADMIN_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)