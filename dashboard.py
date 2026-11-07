"""
Optional lightweight Discord OAuth2 dashboard.
Set DASHBOARD_CLIENT_ID, DASHBOARD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI,
DASHBOARD_SESSION_SECRET, DISCORD_TOKEN. Keep behind HTTPS in production.
This dashboard shows guilds where the logged-in user has Manage Server.
"""
import os, secrets
from urllib.parse import urlencode
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
import aiohttp

app=FastAPI(title="Ultimate Bot Dashboard")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("DASHBOARD_SESSION_SECRET","CHANGE_THIS_SESSION_SECRET"))

CLIENT_ID=os.getenv("DASHBOARD_CLIENT_ID","")
CLIENT_SECRET=os.getenv("DASHBOARD_CLIENT_SECRET","")
REDIRECT=os.getenv("DASHBOARD_REDIRECT_URI","http://localhost:8000/callback")
API="https://discord.com/api/v10"

@app.get("/",response_class=HTMLResponse)
async def home(request: Request):
    user=request.session.get("user")
    if not user:
        return '<h1>Ultimate Bot Dashboard</h1><a href="/login">Login with Discord</a>'
    return f'<h1>Welcome {user["username"]}</h1><p>Dashboard login works. Guild configuration panels can be added here.</p><a href="/guilds">View manageable servers</a> | <a href="/logout">Logout</a>'

@app.get("/login")
async def login(request: Request):
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(500,"Configure dashboard OAuth environment variables.")
    state=secrets.token_urlsafe(24); request.session["oauth_state"]=state
    params={"client_id":CLIENT_ID,"redirect_uri":REDIRECT,"response_type":"code","scope":"identify guilds","state":state}
    return RedirectResponse("https://discord.com/oauth2/authorize?"+urlencode(params))

@app.get("/callback")
async def callback(request: Request, code: str, state: str):
    if not secrets.compare_digest(state,request.session.pop("oauth_state","")):
        raise HTTPException(400,"Invalid OAuth state.")
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{API}/oauth2/token",data={"client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"grant_type":"authorization_code","code":code,"redirect_uri":REDIRECT},headers={"Content-Type":"application/x-www-form-urlencoded"}) as r:
            token=await r.json()
            if r.status!=200: raise HTTPException(400,"OAuth token exchange failed.")
        headers={"Authorization":f"Bearer {token['access_token']}"}
        async with s.get(f"{API}/users/@me",headers=headers) as r: user=await r.json()
        async with s.get(f"{API}/users/@me/guilds",headers=headers) as r: guilds=await r.json()
    request.session["user"]={"id":user["id"],"username":user["username"]}
    request.session["guilds"]=[{"id":g["id"],"name":g["name"]} for g in guilds if int(g["permissions"]) & 0x20]
    return RedirectResponse("/")

@app.get("/guilds",response_class=HTMLResponse)
async def guilds(request: Request):
    if "user" not in request.session: return RedirectResponse("/login")
    items="".join(f"<li>{g['name']} — <code>{g['id']}</code></li>" for g in request.session.get("guilds",[]))
    return f"<h1>Manageable servers</h1><ul>{items}</ul><a href='/'>Home</a>"

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")
