
import os

replacements = [
    # Specific Execution/Completion (moved to sub_agents)
    ("apps.core.src.agent.execution.transfer", "apps.core.src.agent.sub_agents.transfer"),
    ("apps.core.src.agent.completion.transfer", "apps.core.src.agent.sub_agents.transfer.completion"),
    ("apps.core.src.agent.execution.airtime", "apps.core.src.agent.sub_agents.airtime"),
    ("apps.core.src.agent.completion.airtime", "apps.core.src.agent.sub_agents.airtime.completion"),
    ("apps.core.src.agent.execution.onboarding", "apps.core.src.agent.sub_agents.onboarding"),
    
    # Shared Services (moved to tools)
    ("apps.core.src.agent.services.validation_service", "apps.core.src.agent.tools.validation.service"),
    ("apps.core.src.agent.services.user_data_cache", "apps.core.src.agent.tools.cache.user_data"),
    ("apps.core.src.agent.services.account_selection_service", "apps.core.src.agent.tools.account_selection.service"),
    ("apps.core.src.agent.services.cancellation_utils", "apps.core.src.agent.tools.cancellation"),
    ("apps.core.src.agent.services.flow_completion_callback", "apps.core.src.agent.tools.flow_completion"),
    
    # Nodes (moved to tools)
    ("apps.core.src.agent.nodes.account_selection", "apps.core.src.agent.tools.account_selection.node"),
    ("apps.core.src.agent.nodes.context", "apps.core.src.agent.tools.context"),
    
    # Main Agent Directories (moved to sub_agents)
    ("apps.core.src.agent.transfer", "apps.core.src.agent.sub_agents.transfer"),
    ("apps.core.src.agent.airtime", "apps.core.src.agent.sub_agents.airtime"),
    ("apps.core.src.agent.query", "apps.core.src.agent.sub_agents.query"),
    ("apps.core.src.agent.account_management", "apps.core.src.agent.sub_agents.account_management"),
    
    # Shared Tools (moved to tools)
    ("apps.core.src.agent.authorization", "apps.core.src.agent.tools.authorization"),
    ("apps.core.src.agent.beneficiary", "apps.core.src.agent.tools.beneficiary"),
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

