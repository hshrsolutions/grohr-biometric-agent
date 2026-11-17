"""A small local web page for commissioning and checking the agent.

	python -m grohr_agent --ui          then open http://127.0.0.1:8765

Listens on 127.0.0.1 only and refuses any other Host header (DNS-rebinding
guard). It is read-only: it can probe a terminal and preview its newest punches,
and show the spool and log — it cannot change config.json, the terminal, or send
anything to GroHR. Secrets (api_secret, Comm Key replies) are never displayed.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from grohr_agent import __version__
from grohr_agent.devices.zkteco import ZKTecoReader
from grohr_agent.spool import Spool

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GroHR Biometric Agent</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b2430;--mut:#6b7686;--line:#e2e6ec;--acc:#1f6feb;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700}
@media(prefers-color-scheme:dark){:root{--bg:#10151c;--card:#182029;--ink:#e6ebf1;--mut:#93a0b0;--line:#27323f;--acc:#58a6ff;--ok:#3fb950;--bad:#f85149;--warn:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--mut);margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 12px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:end}
label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:4px}
input[type=text],input[type=number],input[type=password]{padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit;width:160px}
.chk{flex-direction:row;align-items:center;gap:6px;font-size:14px;color:var(--ink)}
button{padding:8px 14px;border:0;border-radius:6px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--line)}button:disabled{opacity:.5}
dl{display:grid;grid-template-columns:130px 1fr;gap:4px 12px;margin:12px 0 0}dt{color:var(--mut)}dd{margin:0;word-break:break-all}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:500}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:12px;max-height:260px;margin:10px 0 0}
.stats{display:flex;gap:12px;flex-wrap:wrap}.stat{flex:1;min-width:120px;border:1px solid var(--line);border-radius:8px;padding:10px}.stat b{display:block;font-size:22px}.stat span{color:var(--mut);font-size:12px}
</style></head><body><main>
<h1>GroHR Biometric Agent</h1><div class="sub" id="ver"></div>

<div class="card"><h2>Connect to a ZKTeco / eSSL terminal (LX50)</h2>
<div class="row">
<label>IP address<input type="text" id="ip" placeholder="192.168.1.201"></label>
<label>Port<input type="number" id="port" value="4370"></label>
<label>Comm Key<input type="password" id="pw" value="0"></label>
<label class="chk"><input type="checkbox" id="udp"> Force UDP</label>
<button id="go">Test connection</button>
</div>
<div id="res"></div></div>

<div class="card"><h2>Newest punches on the terminal <span class="sub">(preview only — nothing is sent)</span></h2>
<button class="sec" id="pun" disabled>Load last 20 punches</button><div id="pres"></div></div>

<div class="card"><h2>Agent status</h2><div class="stats" id="stats"></div>
<button class="sec" id="ref" style="margin-top:12px">Refresh</button><pre id="log">…</pre></div>
</main>
<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s??"—").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const body=()=>JSON.stringify({ip:$("ip").value.trim(),port:+$("port").value||4370,password:+$("pw").value||0,udp:$("udp").checked});
async function post(u){const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:body()});return r.json()}
$("go").onclick=async()=>{
  if(!$("ip").value.trim()){$("res").innerHTML='<p class="bad">Enter the terminal IP.</p>';return}
  $("go").disabled=true;$("res").innerHTML='<p>Connecting…</p>';
  try{const d=await post("/api/probe");
    if(!d.ok){$("res").innerHTML='<p class="bad">Could not connect: '+esc(d.error)+'</p><p>Check the IP and port 4370, that this PC is on the terminal\'s network, the Comm Key (Menu → Comm. → Security), or tick Force UDP.</p>';$("pun").disabled=true}
    else{const i=d.info,dr=i.drift_seconds,drc=Math.abs(dr)>300?"warn":"ok";
      $("res").innerHTML='<p class="ok">Connected.</p><dl><dt>Serial</dt><dd>'+esc(i.serial)+' <small>(register this in GroHR)</small></dd><dt>Model</dt><dd>'+esc(i.device)+' · '+esc(i.platform)+'</dd><dt>Firmware</dt><dd>'+esc(i.firmware)+'</dd><dt>MAC</dt><dd>'+esc(i.mac)+'</dd><dt>Clock</dt><dd class="'+drc+'">'+esc(i.clock)+(dr==null?"":' (PC differs by '+dr+'s)')+'</dd><dt>Users</dt><dd>'+esc(i.users)+' / '+esc(i.users_cap)+'</dd><dt>Punches stored</dt><dd>'+esc(i.punches)+' / '+esc(i.punches_cap)+'</dd></dl>'+snippet();
      $("pun").disabled=false}
  }catch(e){$("res").innerHTML='<p class="bad">'+esc(e)+'</p>'}
  $("go").disabled=false};
function snippet(){const pw=+$("pw").value||0,udp=$("udp").checked;if(!pw&&!udp)return"<p>No extra settings needed in config.json.</p>";
  return'<p>Add to <code>config.json</code>:</p><pre>'+esc(JSON.stringify({zkteco:{"*":{password:pw,force_udp:udp}}},null,2))+'</pre>'}
$("pun").onclick=async()=>{$("pun").disabled=true;$("pres").innerHTML="<p>Reading…</p>";
  try{const d=await post("/api/punches");
    if(!d.ok){$("pres").innerHTML='<p class="bad">'+esc(d.error)+'</p>'}
    else if(!d.punches.length){$("pres").innerHTML="<p>The terminal has no punches.</p>"}
    else $("pres").innerHTML="<table><tr><th>User ID</th><th>Time</th><th>State</th></tr>"+d.punches.map(p=>"<tr><td>"+esc(p.device_user_id)+"</td><td>"+esc(p.punch_time)+"</td><td>"+esc(p.raw_log_type)+"</td></tr>").join("")+"</table>"}
  catch(e){$("pres").innerHTML='<p class="bad">'+esc(e)+"</p>"}$("pun").disabled=false};
async function status(){const d=await (await fetch("/api/status")).json();
  $("ver").textContent="Agent "+d.version+(d.site?" · "+d.site:"")+(d.connector?" · connector "+d.connector:"");
  const s=d.spool||{};$("stats").innerHTML=[["Waiting to send",s.pending,""],["Failed (dead)",s.dead,s.dead?"bad":""],["Delivered (remembered)",s.sent,""]].map(x=>'<div class="stat"><b class="'+x[2]+'">'+esc(x[1])+"</b><span>"+x[0]+"</span></div>").join("");
  $("log").textContent=d.log||"No log yet."}
$("ref").onclick=status;status();
</script></body></html>"""


def make_handler(config):
	class Handler(BaseHTTPRequestHandler):
		server_version = "GroHRAgentUI"

		def log_message(self, *a):
			pass

		def _send(self, code, payload, ctype="application/json"):
			data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
			self.send_response(code)
			self.send_header("Content-Type", ctype + "; charset=utf-8")
			self.send_header("Content-Length", str(len(data)))
			self.send_header("Cache-Control", "no-store")
			self.send_header("X-Content-Type-Options", "nosniff")
			self.end_headers()
			self.wfile.write(data)

		def _host_ok(self):
			host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
			return host in ("127.0.0.1", "localhost")

		def do_GET(self):
			if not self._host_ok():
				return self._send(403, {"error": "forbidden"})
			if self.path in ("/", "/index.html"):
				return self._send(200, PAGE, "text/html")
			if self.path == "/api/status":
				return self._send(200, status(config))
			self._send(404, {"error": "not found"})

		def do_POST(self):
			if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
				return self._send(403, {"error": "forbidden"})
			try:
				size = min(int(self.headers.get("Content-Length") or 0), 4096)
				req = json.loads(self.rfile.read(size) or b"{}")
				device = {
					"name": "ui", "serial_no": None, "ip_address": str(req["ip"]),
					"port": int(req.get("port") or 4370),
					"zk": {"password": int(req.get("password") or 0), "force_udp": bool(req.get("udp")),
					       "timeout": 10, "verify_serial": False},
				}
				reader = ZKTecoReader(device, None)
				if self.path == "/api/probe":
					return self._send(200, {"ok": True, "info": reader.describe()})
				if self.path == "/api/punches":
					return self._send(200, {"ok": True, "punches": reader.recent(20)})
				self._send(404, {"error": "not found"})
			except Exception as e:
				self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

	return Handler


def status(config):
	counts = {}
	try:
		spool = Spool(config.spool_path)
		counts = spool.counts()
		spool.close()
	except Exception:
		pass
	log = ""
	try:
		with open(os.path.join(config.log_dir, "agent.log"), encoding="utf-8", errors="replace") as handle:
			log = "".join(handle.readlines()[-40:])
	except OSError:
		pass
	return {"version": __version__, "site": config.get("site_url"), "connector": config.get("connector"),
	        "spool": counts, "log": log}


def serve(config, port=8765):
	httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(config))
	print(f"GroHR agent UI: http://127.0.0.1:{port}  (Ctrl+C to stop)")
	try:
		httpd.serve_forever()
	except KeyboardInterrupt:
		pass
	return 0
