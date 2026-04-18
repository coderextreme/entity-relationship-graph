from flask import Flask, render_template, request, jsonify
import json
import os
import re
from typing import Any, Dict, List, Union

app = Flask(__name__)

# Store the JSON data in memory (for simplicity)
json_data = None
current_path = "/"
path_history = ["/"]
history_index = 0

@app.route('/')
def index():
    """Renders the main page."""
    return render_template('index3.html', data=get_tree_data(current_path))

@app.route('/upload', methods=['POST'])
def upload():
    """Handles the JSON file upload."""
    global json_data, current_path, path_history, history_index

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'})

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'})

    try:
        json_data = json.load(file)
        current_path = "/"
        path_history = ["/"]
        history_index = 0
        return jsonify({'success': True, 'data': get_tree_data(current_path)})

    except Exception as e:
        return jsonify({'error': f'Failed to load JSON: {str(e)}'})

@app.route('/navigate', methods=['POST'])
def navigate():
    """Handles navigation requests."""
    global current_path, path_history, history_index

    item_text = request.form.get('item_text')
    new_path = os.path.join(current_path, item_text).replace("\\", "/")

    try:
        # Update history
        path_history = path_history[:history_index + 1]
        path_history.append(new_path)
        history_index += 1
        current_path = new_path

        return jsonify({'success': True, 'data': get_tree_data(current_path)})

    except KeyError:
        return jsonify({'error': f'Invalid path: {new_path}'})
    except TypeError:
        return jsonify({'error': 'Cannot navigate to a non-object/array.'})
    except Exception as e:
        return jsonify({'error': f'Error navigating: {str(e)}'})

@app.route('/back', methods=['POST'])
def back():
    """Handles navigation back."""
    global current_path, history_index

    if history_index > 0:
        history_index -= 1
        current_path = path_history[history_index]
        return jsonify({'success': True, 'data': get_tree_data(current_path)})
    else:
        return jsonify({'success': False})  # Already at the beginning

@app.route('/forward', methods=['POST'])
def forward():
    """Handles navigation forward."""
    global current_path, history_index

    if history_index < len(path_history) - 1:
        history_index += 1
        current_path = path_history[history_index]
        return jsonify({'success': True, 'data': get_tree_data(current_path)})
    else:
        return jsonify({'success': False})  # Already at the end

def get_data_from_path(path: str) -> Union[Dict[str, Any], List[Any]]:
    """Retrieves data from the JSON structure based on the specified path."""
    global json_data

    if path == "/":
        return json_data

    parts = path.split("/")[1:]
    data = json_data

    for part in parts:
        try:
            index = int(part.strip("[]"))
            data = data[index]
        except ValueError:
            data = data[part]

    return data

def get_tree_data(path: str) -> List[Dict[str, Any]]:
    """Generates the data for the treeview based on the path."""
    data = get_data_from_path(path)
    tree_data = []

    if isinstance(data, dict):
        for key, value in data.items():
            tree_data.append({
                'name': key,
                'type': get_type_string(value),
                'value': process_value(value),  # Process the value for URLs
                'children': isinstance(value, (dict, list))
            })
    elif isinstance(data, list):
        for i, value in enumerate(data):
            tree_data.append({
                'name': f'[{i}]',
                'type': get_type_string(value),
                'value': process_value(value),  # Process the value for URLs
                'children': isinstance(value, (dict, list))
            })

    return tree_data


def process_value(value: Any) -> Union[str, Dict[str, str]]:
    """Processes a value to detect and mark URLs of different types."""
    if isinstance(value, str):
        if re.match(r"https?://.*\.(jpg|jpeg|png|gif)$", value, re.IGNORECASE):
            return {"type": "image", "url": value}
        elif re.match(r"https?://.*\.(mp4|webm|ogg)$", value, re.IGNORECASE):
            return {"type": "video", "url": value}
        elif re.match(r"https?://.*\.(x3d)$", value, re.IGNORECASE):
            return {"type": "x3d", "url": value}
        elif re.match(r"https?://.*\.(wrl|vrml|x3dv)$", value, re.IGNORECASE):
            return {"type": "vrml", "url": value}
        elif re.match(r"https://[^\s]+", value):  # Basic URL
            return {"type": "url", "url": value}

        else:
            return value
    else:
        return str(value)  # Convert other types to strings


def get_type_string(value: Any) -> str:
    """Returns a string representation of the data type."""
    if isinstance(value, dict):
        return "Object"
    elif isinstance(value, list):
        return "Array"
    elif isinstance(value, str):
        return "String"
    elif isinstance(value, int):
        return "Integer"
    elif isinstance(value, float):
        return "Float"
    elif isinstance(value, bool):
        return "Boolean"
    elif value is None:
        return "Null"
    else:
        return "Unknown"


if __name__ == '__main__':
    app.run(debug=True)
