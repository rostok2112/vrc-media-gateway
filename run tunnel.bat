if not exist logs mkdir logs
type nul > logs\cloudflared.log
start "cloudflared" cmd /k ^
cloudflared tunnel --url http://127.0.0.1:8080 ^
  --loglevel debug ^
  --transport-loglevel debug ^
  --logfile logs\cloudflared.log
