
import os

replacements = [
    ("apps.core.src.agent.batch.executor", "apps.core.src.agent.tools.batch.executor"),
    ("apps.core.src.agent.batch.utils", "apps.core.src.agent.tools.batch.utils"),
    ("apps.core.src.agent.batch", "apps.core.src.agent.tools.batch"),
]

def update_file(filepath):
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        new_content = content
        for old, new in replacements:
            new_content = new_content.replace(old, new)
            
        if new_content != content:
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Updated: {filepath}")
    except Exception as e:
        print(f"Error processing {filepath}: {e}")

def main():
    target_dir = "apps/core/src"
    for root, dirs, files in os.walk(target_dir):
        for file in files:
            if file.endswith(".py"):
                update_file(os.path.join(root, file))

if __name__ == "__main__":
    main()

