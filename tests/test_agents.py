# Quick agent connection test
import sys
sys.path.insert(0, "omnichat")
from src.connectors import create_all_agents

print("=== AGENT CHECK ===")
agents = create_all_agents()
for name, agent in agents.items():
    status = "[OK]" if agent.is_connected else "[FAIL]"
    err = f" ({agent.error_message})" if agent.error_message else ""
    print(f"{status} {name.upper()}{err}")

connected = sum(1 for a in agents.values() if a.is_connected)
print(f"Connected: {connected}/3")
