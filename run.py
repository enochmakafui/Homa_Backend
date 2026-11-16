import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # 9004 matches the Vite dev proxy in the frontend (`/api` -> http://localhost:9004).
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", "9004")))
