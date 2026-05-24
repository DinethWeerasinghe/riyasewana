"""
app.py - Flask application entry point

Routes:
    GET  /                          - Serve the main UI
    POST /api/scrape                - SSE stream: real-time progress + analysis result
    GET  /api/download/<session_id> - Download two-section analysis CSV
"""

import json
import queue
import asyncio
import os
import threading
from datetime import datetime

from flask import Flask, request, Response, render_template, make_response, stream_with_context
from flask_cors import CORS

from scraper import scrape_riyasewana_task
from analyzer import analyze_data, build_download_excel

app = Flask(__name__, template_folder='.')  # serve index.html from root
CORS(app)

# In-memory session cache: session_id -> {'yearly_stats': [...], 'raw_data': [...]}
session_cache: dict = {}


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Scrape + Analyze — Server-Sent Events stream
# ---------------------------------------------------------------------------

@app.route('/api/scrape', methods=['POST'])
def scrape():
    """
    Streams real-time progress events (SSE) while scraping and analysing.
    Final event has step='done' and contains the full analysis JSON.
    Error events have step='error'.
    """
    data = request.json or {}
    progress_q = queue.Queue()
    result_holder: dict = {}

    def run_scraping():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            cars = loop.run_until_complete(scrape_riyasewana_task(data, progress_q))
            result_holder['cars'] = cars
            result_holder['success'] = True
        except Exception as e:
            result_holder['success'] = False
            result_holder['error'] = str(e)
            print(f"[ERROR] Scraping failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            progress_q.put(None)  # sentinel — signals generator to stop waiting

    thread = threading.Thread(target=run_scraping, daemon=True)
    thread.start()

    def generate():
        # Forward progress events until sentinel
        while True:
            try:
                msg = progress_q.get(timeout=60)
                if msg is None:
                    break
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                # Keep connection alive with a comment
                yield ": heartbeat\n\n"

        thread.join(timeout=5)

        # Handle scraping failure
        if not result_holder.get('success'):
            error = result_holder.get('error', 'Scraping failed')
            yield f"data: {json.dumps({'step': 'error', 'message': error})}\n\n"
            return

        cars = result_holder.get('cars', [])
        if not cars:
            yield f"data: {json.dumps({'step': 'error', 'message': 'No cars found matching your criteria. Try broadening your filters.'})}\n\n"
            return

        # Emit analysis step
        yield f"data: {json.dumps({'step': 'analyzing', 'message': f'Analysing {len(cars)} listings...'})}\n\n"

        try:
            analysis = analyze_data(cars)
            if 'error' in analysis:
                yield f"data: {json.dumps({'step': 'error', 'message': analysis['error']})}\n\n"
                return

            # Cache for download (raw_data stored server-side only)
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            session_cache[session_id] = {
                'yearly_stats': analysis['yearly_stats'],
                'raw_data': cars,
            }

            # Emit final result (no raw_data in stream — too large)
            yield f"data: {json.dumps({'step': 'done', 'success': True, 'total_cars': len(cars), 'session_id': session_id, 'analysis': analysis})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'step': 'error', 'message': f'Analysis failed: {e}'})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


# ---------------------------------------------------------------------------
# Download two-section analysis CSV
# ---------------------------------------------------------------------------

@app.route('/api/download/<session_id>', methods=['GET'])
def download(session_id):
    """Return a styled two-sheet Excel workbook (.xlsx)."""
    cached = session_cache.get(session_id)
    if not cached:
        return json.dumps({'error': 'Session not found or expired'}), 404, {'Content-Type': 'application/json'}

    try:
        xlsx_bytes = build_download_excel(cached['yearly_stats'], cached['raw_data'])
        response = make_response(xlsx_bytes)
        response.headers['Content-Type'] = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response.headers['Content-Disposition'] = (
            f'attachment; filename="car_analysis_{session_id}.xlsx"'
        )
        return response
    except Exception as e:
        return json.dumps({'error': str(e)}), 500, {'Content-Type': 'application/json'}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("[*] Starting Flask server...")
    print("[*] Open http://localhost:5000 in your browser")
    app.run(debug=True, port=5000, threaded=True)