#!/bin/bash
echo "🔄 Testing integrated system..."

# Stop orice containere anterioare
docker stop watergrid-final streamlit-debug 2>/dev/null

# Launch final integrated container
docker run -d --name watergrid-integrated \
  --network siret-monitor_default \
  -p 127.0.0.1:8501:8501 \
  -v "$(pwd):/app/streamlit" \
  -e WATERGRID_API_URL=http://api:8000/api \
  siret-backend:latest \
  streamlit run /app/streamlit/app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true

sleep 15

echo "=== INTEGRATED SYSTEM TEST ==="
curl -k -s https://water.noze.ro/ | grep -q "WaterGrid\|land.change" && echo "🎉 INTEGRATED SUCCESS!" || echo "Checking startup..."

echo "=== CONTAINER LOGS ==="
docker logs watergrid-integrated --tail 15
