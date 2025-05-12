import os
import io
import cv2
import threading
import time
import queue
from flask import Flask, Response, jsonify, send_file, request, abort

# Environment variables
RTSP_URL = os.environ.get("RTSP_URL")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "80"))

if RTSP_URL is None:
    raise RuntimeError("RTSP_URL environment variable must be set")

app = Flask(__name__)

class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.running = False
        self.frame = None
        self.lock = threading.Lock()
        self.thread = None
        self.last_frame_time = 0
        self.fps = 15  # Limit FPS for streaming
        self.subscribers = 0

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._update, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.cap is not None:
                self.cap.release()
                self.cap = None

    def _update(self):
        self.cap = cv2.VideoCapture(self.rtsp_url)
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            self.running = False
            return
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                # Try to reconnect
                self.cap.release()
                time.sleep(1)
                self.cap = cv2.VideoCapture(self.rtsp_url)
                continue
            with self.lock:
                self.frame = frame
                self.last_frame_time = time.time()
            time.sleep(1.0 / self.fps)
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def get_frame(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def get_jpeg(self):
        frame = self.get_frame()
        if frame is None:
            return None
        ret, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ret:
            return None
        return jpeg.tobytes()

    def add_subscriber(self):
        self.subscribers += 1
        if self.subscribers == 1:
            self.start()

    def remove_subscriber(self):
        self.subscribers = max(0, self.subscribers - 1)
        if self.subscribers == 0:
            self.stop()

camera = CameraStream(RTSP_URL)

@app.route("/info", methods=["GET"])
def info():
    return jsonify({
        "device_name": "RTSP Camera",
        "device_model": "RTSP Camera",
        "manufacturer": "Generic",
        "device_type": "IP Camera",
        "protocol": "RTSP",
        "data_points": "video stream",
        "commands": ["start stream", "stop stream"]
    })

@app.route("/capture", methods=["POST"])
def capture():
    camera.add_subscriber()
    time.sleep(0.1)  # Wait for at least 1 frame
    jpeg = camera.get_jpeg()
    camera.remove_subscriber()
    if jpeg is None:
        abort(503, description="Camera not ready")
    return send_file(
        io.BytesIO(jpeg),
        mimetype='image/jpeg',
        as_attachment=True,
        download_name="capture.jpg"
    )

def gen_mjpeg_stream():
    camera.add_subscriber()
    try:
        while True:
            frame = camera.get_jpeg()
            if frame is None:
                time.sleep(0.1)
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            # Throttle FPS
            time.sleep(1.0 / camera.fps)
    finally:
        camera.remove_subscriber()

@app.route("/stream", methods=["POST"])
def stream():
    return Response(gen_mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)