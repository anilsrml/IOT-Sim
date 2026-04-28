# IOT-Sim

Akıllı ev otonom havalandırma simülasyonu (MQTT + JSON + Dashboard).

## Hızlı Başlatma (Windows / PowerShell)

```powershell
cd "IOT_sim"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
docker compose -f broker/docker-compose.yml up -d
```

## Servisleri Çalıştır

Terminal-1:

```powershell
python subscriber/main.py
```

Terminal-2:

```powershell
python publisher/main.py
```

## Panel

- Dashboard: `http://localhost:8000`
- MQTT topic: `team01/telemetry` (veya `.env` içindeki `TEAM_NO`)
