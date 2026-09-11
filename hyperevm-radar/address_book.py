import json

PATH = "/opt/hyperevm-radar/addresses.json"

def load_deployers():
    try:
        with open(PATH, "r") as f:
            data = json.load(f)
        return {str(x).lower() for x in data.get("deployers", [])}
    except Exception as e:
        print(f"Address book load error: {e}")
        return set()

def save_deployer(addr):
    try:
        with open(PATH, "r") as f:
            data = json.load(f)

        deployers = {str(x).lower() for x in data.get("deployers", [])}
        addr = str(addr).lower()

        if addr not in deployers:
            deployers.add(addr)
            with open(PATH, "w") as f:
                json.dump({"deployers": sorted(deployers)}, f, indent=2)
            print(f"Address book added: {addr}")

    except Exception as e:
        print(f"Address book save error: {e}")

def auto_add_if_high_frequency(creator, recent_count):
    if recent_count >= 3:
        save_deployer(creator)
        return True
    return False
