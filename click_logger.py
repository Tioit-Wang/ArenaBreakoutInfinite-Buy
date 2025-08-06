import json
import datetime
from pynput import mouse, keyboard

class KeyMapper:
    def __init__(self, output_file="key_mapping.json"):
        self.is_recording = False
        self.next_mapping_key = None
        self.key_mappings = {}
        self.output_file = output_file
        self.mapping_keys = {
            '1': '市场入口',
            '2': '搜索入口',
            '3': '搜索按钮',
            '4': '第一个商品位置',
            '5': '商品刷新位置',
            '6': '商品关闭位置',
        }

    def save_mappings(self):
        """Saves the current key mappings to a JSON file."""
        try:
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(self.key_mappings, f, ensure_ascii=False, indent=4)
            print(f"--- Mappings saved to {self.output_file} ---")
        except Exception as e:
            print(f"Error saving mappings: {e}")

    def on_click(self, x, y, button, pressed):
        """Callback for mouse click events."""
        if self.is_recording and self.next_mapping_key and pressed:
            print(f"Mapping '{self.next_mapping_key}' to coordinates: ({x}, {y})")
            self.key_mappings[self.next_mapping_key] = {'x': x, 'y': y}
            self.next_mapping_key = None
            print("Please press another number key (1-6) or F4 to finish.")

    def on_press(self, key):
        """Callback for keyboard press events."""
        if key == keyboard.Key.f4:
            self.is_recording = not self.is_recording
            if self.is_recording:
                print("--- Started key mapping mode. Press 1-6 to map a key. ---")
            else:
                print("--- Stopped key mapping mode. ---")
                self.save_mappings()
            return

        if self.is_recording:
            try:
                char_key = key.char
                if char_key in self.mapping_keys:
                    self.next_mapping_key = self.mapping_keys[char_key]
                    print(f"Ready to map '{self.next_mapping_key}'. Click the target location.")
            except AttributeError:
                pass  # Ignore special keys other than F4

        if key == keyboard.Key.esc:
            print('--- Escape key pressed. Stopping listeners. ---')
            return False  # Stop the listener

    def start(self):
        """Starts the mouse and keyboard listeners."""
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press)

        self.mouse_listener.start()
        self.keyboard_listener.start()

        print("--- Listener started. ---")
        print("Press F4 to start/stop recording key mappings.")
        print("Press Esc to exit the program.")
        
        self.keyboard_listener.join()
        self.mouse_listener.stop()


if __name__ == "__main__":
    mapper = KeyMapper()
    mapper.start()
    print("--- Program finished. ---")
