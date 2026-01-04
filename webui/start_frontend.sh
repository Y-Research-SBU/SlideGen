#!/bin/bash

cd "$(dirname "$0")/frontend" 2>/dev/null || cd frontend

echo "Cleaning and installing frontend dependencies..."
rm -rf node_modules package-lock.json
npm install

echo "Starting SlideGen WebUI frontend..."
echo "Frontend will be available at: http://localhost:3000"
echo ""

npm run dev