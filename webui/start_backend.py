#!/usr/bin/env python3

import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    if not (project_root / "src").exists():
        print("Error: Please run this script from the SlideGen project root")
        sys.exit(1)
    
    os.chdir(project_root)
    
    env_file = project_root / ".env"
    if not env_file.exists():
        print("Warning: .env file not found. Make sure your API keys are set.")
    
    if not (project_root / "config").exists():
        print("Error: config directory not found. Make sure you're in the SlideGen root.")
        sys.exit(1)
    
    print("Starting SlideGen WebUI backend...")
    print(f"Working directory: {os.getcwd()}")
    print("Backend will be available at: http://localhost:8000")
    print("API docs at: http://localhost:8000/docs")
    print()
    
    import uvicorn
    from backend.main import app
    
    uvicorn.run(app, host="0.0.0.0", port=8000)