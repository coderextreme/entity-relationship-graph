from flask import Flask, render_template, request, jsonify
import json
import os
import re
from typing import Any, Dict, List, Union

app = Flask(__name__)

# Store the extracted media URLs
media_urls = []

@app.route('/')
def index():
    """Renders the main page with media URLs."""
    return render_template('index4.html', media_urls=media_urls)


@app.route('/upload', methods=['POST'])
def upload():
    """Handles the JSON file upload."""
    global json_data, media_urls
    media_urls = [] #Reset global media_urls

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'})

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'})

    try:
        json_data = json.load(file)
        extract_media_urls(json_data)  # Extract media URLs from JSON data
        return render_template('index.html', media_urls=media_urls)  # Pass URLs to template

    except Exception as e:
        return render_template('index.html', error=f'Failed to load JSON: {str(e)}', media_urls=[]) # Return an empty list if failed

# In extract_media_urls
def extract_media_urls(data: Union[Dict, List, Any]):
    """Recursively extracts image, video, X3D, and VRML URLs from JSON data."""
    global media_urls

    if isinstance(data, dict):
        for value in data.values():
            extract_media_urls(value)  # Recursive call for each value
    elif isinstance(data, list):
        for item in data:
            extract_media_urls(item)  # Recursive call for each item
    elif isinstance(data, str):
        media_type = get_media_type(data)
        if media_type:
            media_urls.append({"type": media_type, "url": data})  # Add the URL and type
            print(f"Found media URL: {data} (Type: {media_type})") # ADDED PRINT
# In app.route(/upload)
        print(f"Found {len(media_urls)} media URLs.") # ADDED PRINT


def get_media_type(url: str) -> str:
    """Determines the media type from a URL."""
    if re.match(r"https?://.*\.(jpg|jpeg|png|gif)$", url, re.IGNORECASE):
        return "image"
    elif re.match(r"https?://.*\.(mp4|webm|ogg)$", url, re.IGNORECASE):
        return "video"
    elif re.match(r"https?://.*\.(x3d)$", url, re.IGNORECASE):
        return "x3d"
    elif re.match(r"https?://.*\.(wrl|vrml|x3dv)$", url, re.IGNORECASE):
        return "vrml"
    else:
        return None  # Not a recognized media type

if __name__ == '__main__':
    app.run(debug=True)
