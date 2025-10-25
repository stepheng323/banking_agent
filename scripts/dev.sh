#!/bin/bash

echo "🚀 Starting Banking Agent Development Environment"

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from example..."
    cp .env.example .env
    echo "⚠️  Please update .env with your actual values"
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Start Gateway Service (Port 8000)
echo "Starting Gateway Service (Port 8000)..."
cd gateway
poetry run uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
GATEWAY_PID=$!

# Start Core Service (Port 8001)
echo "Starting Core Service (Port 8001)..."
cd ../core
poetry run uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload &
CORE_PID=$!

cd ..

echo ""
echo "✅ Services started!"
echo "Gateway Service: http://localhost:8000"
echo "Core Service: http://localhost:8001"
echo "Gateway Docs: http://localhost:8000/docs"
echo "Core Docs: http://localhost:8001/docs"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for interrupt
trap "echo '🛑 Stopping services...'; kill $GATEWAY_PID $CORE_PID; exit" INT
wait
