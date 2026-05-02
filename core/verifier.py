import cv2
import os
import numpy as np
from skimage.metrics import structural_similarity as ssim
from core.db_manager import DBManager


class VideoVerifier:
    def __init__(self):
        self.db = DBManager()
        self.bad_frames_dir = "assets/bad_frames"
        os.makedirs(self.bad_frames_dir, exist_ok=True)

        # 1. CREATE A REFERENCE "BAD" IMAGE
        # We generate a dummy 'bad' image to compare against
        # (This matches the fallback logic in visuals.py)
        self.reference_bad_path = os.path.join(
            self.bad_frames_dir, "reference_error.jpg"
        )
        self._create_reference_image()

    def _create_reference_image(self):
        """Generates a dummy 'Visual Unavailable' image to compare against."""
        if not os.path.exists(self.reference_bad_path):
            # Create a generic dark image similar to your placeholder
            # Note: For best results, actually save a REAL screenshot of your error card
            # and overwrite this file!
            blank_image = np.zeros((1920, 1080, 3), np.uint8)
            blank_image[:] = (10, 10, 20)  # Your background color
            cv2.imwrite(self.reference_bad_path, blank_image)

    def is_frame_bad(self, frame):
        """Checks if a single frame matches the 'Bad Reference'."""
        # 1. Check for Black Screen (Mean pixel intensity < 5)
        if np.mean(frame) < 5:
            return True, "Black Screen"

        # 2. Check against Reference Error Image (Structural Similarity)
        # We resize frame to match reference for comparison
        ref_img = cv2.imread(self.reference_bad_path)
        if ref_img is None:
            return False, ""

        # Resize for speed (compare small thumbnails)
        frame_small = cv2.resize(frame, (100, 100))
        ref_small = cv2.resize(ref_img, (100, 100))

        # Convert to grayscale
        gray_frame = cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY)
        gray_ref = cv2.cvtColor(ref_small, cv2.COLOR_BGR2GRAY)

        # Compute Similarity (0.0 to 1.0)
        score, _ = ssim(gray_frame, gray_ref, full=True)

        # If 80% similar to error card -> IT IS BAD
        if score > 0.80:
            return True, "Error Placeholder Detected"

        return False, ""

    def verify(self):
        task = self.db.collection.find_one({"status": "completed"})
        if not task:
            print("📭 No completed videos to verify.")
            return

        video_path = task.get("final_video_path")
        if not video_path or not os.path.exists(video_path):
            print("❌ Error: Video file missing.")
            return

        print(f"🧐 Verifying Quality: {os.path.basename(video_path)}...")

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        is_clean = True
        error_reason = ""

        # Scan 1 frame every second (Checking every single frame is too slow)
        step = int(fps)

        for i in range(0, total_frames, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                break

            bad, reason = self.is_frame_bad(frame)
            if bad:
                is_clean = False
                error_reason = reason
                print(f"   ❌ FAILED at {i/fps:.1f}s: {reason}")
                break

        cap.release()

        if is_clean:
            print("   ✅ QC PASSED: Video is clean.")
            self.db.collection.update_one(
                {"_id": task["_id"]}, {"$set": {"status": "ready_to_upload"}}
            )
        else:
            print("   ⛔ QC FAILED: Moving to 'review' pile.")
            self.db.collection.update_one(
                {"_id": task["_id"]},
                {"$set": {"status": "failed_qc", "qc_reason": error_reason}},
            )
            # Optional: Rename file to mark it as bad
            bad_path = video_path.replace(".mp4", "_FAILED.mp4")
            os.rename(video_path, bad_path)


if __name__ == "__main__":
    VideoVerifier().verify()
