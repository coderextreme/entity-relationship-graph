import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, Dict, List, Union


class JsonFileSystemExplorer:
    """
    A simple file system explorer for JSON files.
    Represents JSON objects and arrays as folders, and other data types as files.
    """

    def __init__(self, root: tk.Tk):
        """
        Initializes the JsonFileSystemExplorer.

        Args:
            root: The Tkinter root window.
        """
        self.root = root
        self.root.title("JSON File System Explorer")
        self.current_path = "/"  # Represents the root of the JSON structure
        self.json_data = None  # Will hold the parsed JSON data

        # UI Elements
        self.toolbar = ttk.Frame(self.root)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)

        self.open_button = ttk.Button(self.toolbar, text="Open JSON", command=self.open_json_file)
        self.open_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.back_button = ttk.Button(self.toolbar, text="Back", command=self.go_back, state=tk.DISABLED)
        self.back_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.forward_button = ttk.Button(self.toolbar, text="Forward", command=self.go_forward, state=tk.DISABLED)
        self.forward_button.pack(side=tk.LEFT, padx=5, pady=5)


        self.tree = ttk.Treeview(self.root, columns=("Type", "Value"), show="tree headings")
        self.tree.heading("#0", text="Name")  # The first column is the tree structure
        self.tree.heading("Type", text="Type")
        self.tree.heading("Value", text="Value")  # Only for simple values (files)

        self.tree.column("#0", width=250)
        self.tree.column("Type", width=100)
        self.tree.column("Value", width=200)

        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.on_tree_double_click)  # Double click to navigate
        self.tree.bind("<Open>", self.on_tree_expand) # Populate on expand

        self.path_history = ["/"]  # Keep track of the navigation path
        self.history_index = 0      # Index of the current location in the history



    def open_json_file(self):
        """
        Opens a JSON file using a file dialog and loads its data.
        """
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                with open(file_path, "r") as f:
                    self.json_data = json.load(f)
                self.current_path = "/"
                self.path_history = ["/"]
                self.history_index = 0
                self.populate_tree(self.json_data, "")  # Start at the root
                self.enable_disable_navigation_buttons()


            except Exception as e:
                messagebox.showerror("Error", f"Failed to load JSON: {e}")

    def populate_tree(self, data: Union[Dict[str, Any], List[Any]], parent_node: str):
        """
        Populates the Treeview with data from the JSON object.

        Args:
            data: The JSON data (dictionary or list) to display.
            parent_node: The ID of the parent node in the Treeview.  Empty string for the root.
        """
        self.tree.delete(*self.tree.get_children(parent_node))  # Clear existing items in parent

        if isinstance(data, dict):
            for key, value in data.items():
                node_id = self.tree.insert(parent_node, "end", text=key, values=(self.get_type_string(value), ""))  # Folders don't have a value displayed
                if isinstance(value, (dict, list)):
                    # Add a dummy node so the folder expands (like in a real file system)
                   pass #self.tree.insert(node_id, "end", text="Loading...", values=("", ""))
                else:
                    # This is a "file" (simple data type)
                    self.tree.set(node_id, "Value", str(value))  # Display the value


        elif isinstance(data, list):
            for i, value in enumerate(data):
                node_id = self.tree.insert(parent_node, "end", text=f"[{i}]", values=(self.get_type_string(value), ""))
                if isinstance(value, (dict, list)):
                     # Add a dummy node so the folder expands (like in a real file system)
                    pass # self.tree.insert(node_id, "end", text="Loading...", values=("", ""))
                else:
                    # This is a "file" (simple data type)
                    self.tree.set(node_id, "Value", str(value))


    def get_type_string(self, value: Any) -> str:
        """
        Returns a string representation of the data type.
        """
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

    def on_tree_double_click(self, event: tk.Event):
        """
        Handles double-clicks on tree items.  Navigates into folders (objects/arrays).
        """
        item_id = self.tree.selection()[0]  # Get the selected item's ID

        item_text = self.tree.item(item_id, "text")  # Get the item's name
        item_type = self.tree.item(item_id, "values")[0]  # Get the item's type

        if item_type in ("Object", "Array"):
            # Navigate into the "folder"
            self.navigate_to_item(item_text)


    def navigate_to_item(self, item_text: str):
        """
        Navigates to the specified item (object or array) in the JSON structure.
        """
        if self.json_data is None:
            return

        new_path = os.path.join(self.current_path, item_text)
        new_path = new_path.replace("\\", "/")  # Standardize path separators

        try:
            # Navigate to the new path in the JSON data
            data_to_display = self.get_data_from_path(new_path)

            # Update history
            self.path_history = self.path_history[:self.history_index + 1]  # Remove forward history
            self.path_history.append(new_path)
            self.history_index += 1
            self.current_path = new_path

            #self.populate_tree(data_to_display, "")  # Repopulate the tree
            # The tree structure is already correct so don't repopulate

            self.enable_disable_navigation_buttons()

        except KeyError:
            messagebox.showerror("Error", f"Invalid path: {new_path}")
        except TypeError:
            messagebox.showerror("Error", "Cannot navigate to a non-object/array.")
        except Exception as e:
            messagebox.showerror("Error", f"Error navigating: {e}")

    def get_data_from_path(self, path: str) -> Union[Dict[str, Any], List[Any]]:
        """
        Retrieves data from the JSON structure based on the specified path.

        Args:
            path: The path to the desired data (e.g., "/key1/key2").

        Returns:
            The data at the specified path.

        Raises:
            KeyError: If the path is invalid.
        """

        if path == "/":
            return self.json_data

        parts = path.split("/")[1:]  # Split the path into components, removing the leading slash
        data = self.json_data

        for part in parts:
            try:
                # Try to convert to an integer for array access
                index = int(part.strip("[]"))  # Strip brackets for list indexes
                data = data[index]
            except ValueError:
                # It's a dictionary key
                data = data[part]

        return data


    def go_back(self):
        """
        Navigates back in the history.
        """
        if self.history_index > 0:
            self.history_index -= 1
            self.current_path = self.path_history[self.history_index]
            data_to_display = self.get_data_from_path(self.current_path)
            #self.populate_tree(data_to_display, "")  # Repopulate the tree - now not needed as the tree is already populated when loading the file
            self.enable_disable_navigation_buttons()

    def go_forward(self):
        """
        Navigates forward in the history.
        """
        if self.history_index < len(self.path_history) - 1:
            self.history_index += 1
            self.current_path = self.path_history[self.history_index]
            data_to_display = self.get_data_from_path(self.current_path)
            #self.populate_tree(data_to_display, "")  # Repopulate the tree - now not needed as the tree is already populated when loading the file
            self.enable_disable_navigation_buttons()

    def enable_disable_navigation_buttons(self):
        """
        Enables or disables the back and forward buttons based on the history.
        """
        self.back_button["state"] = tk.NORMAL if self.history_index > 0 else tk.DISABLED
        self.forward_button["state"] = tk.NORMAL if self.history_index < len(self.path_history) - 1 else tk.DISABLED

    def on_tree_expand(self, event):
        """Handles the expand event of a tree node."""
        item_id = self.tree.selection()[0]  # The item being expanded
        item_type = self.tree.item(item_id, "values")[0] # Get the item type.

        if item_type in ("Object", "Array"): # Only populate objects and arrays
            item_text = self.tree.item(item_id, "text")
            new_path = os.path.join(self.current_path, item_text)
            new_path = new_path.replace("\\", "/")

            try:
                data_to_display = self.get_data_from_path(new_path)
                self.populate_tree(data_to_display, item_id)

            except Exception as e:
                messagebox.showerror("Error", f"Error populating: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    explorer = JsonFileSystemExplorer(root)
    root.mainloop()
