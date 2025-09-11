#!/usr/bin/env python3
"""
GenAI Foundry CDK CLI Deployment Tool
Interactive CLI for deploying CDK stacks
"""

import subprocess
import sys
from typing import Dict

# --- Auto-install questionary if not found ---
try:
    import questionary
except ImportError:
    print("📦 Installing required library: questionary...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "questionary"])
    import site
    site.addsitedir(site.getusersitepackages())  # Ensure Python can see user site packages
    import questionary

# Stack configuration mapping
STACKS = {
    "🏦 Banking Stack": {
        "stack_name": "GenAiFoundryBankingStack",
        "description": "Deploy banking-related infrastructure and services",
        "selection_id": "banking"
    },
    "🛡️ Insurance Stack": {
        "stack_name": "GenAiFoundryInsuranceStack", 
        "description": "Deploy insurance-related infrastructure and services",
        "selection_id": "insurance"
    },
    "🛍️ Retail Stack": {
        "stack_name": "GenAiFoundryRetailStack",
        "description": "Deploy retail-related infrastructure and services",
        "selection_id": "retail"
    },
    "🏥 Healthcare Stack": {
        "stack_name": "GenAiFoundryHealthcareStack",
        "description": "Deploy healthcare-related infrastructure and services",
        "selection_id": "healthcare"
    }
}

def deploy_stack(stack_name: str, selection_id: str) -> None:
    """
    Deploy the selected CDK stack with selection context
    """
    print(f"🚀 Deploying stack: {stack_name}")
    print(f"📋 Selection ID: {selection_id}")
    print("⏳ This may take several minutes...")
    
    try:
        # Set environment variable for the stack to use
        import os
        os.environ["CDK_STACK_SELECTION"] = selection_id
        print(f"🔧 Set CDK_STACK_SELECTION={selection_id}")
        
        # Deploy with context
        print("🌐 Deploying to AWS...")
        subprocess.run(f"cdk deploy {stack_name} --require-approval never", shell=True, check=True)
        
        print(f"✅ Stack '{stack_name}' deployed successfully!")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)
    finally:
        # Clean up environment variable
        import os
        if "CDK_STACK_SELECTION" in os.environ:
            del os.environ["CDK_STACK_SELECTION"]

def get_deployment_confirmation(stack_info: Dict[str, str]) -> bool:
    """
    Get final confirmation before deployment
    """
    print(f"\n📝 Deployment Summary:")
    print(f"   Stack: {stack_info['stack_name']}")
    print(f"   Description: {stack_info['description']}")
    print("⚠️  This will create AWS resources that may incur costs.")
    
    return questionary.confirm(
        "Do you want to proceed with deployment?",
        default=False
    ).ask()

def main() -> None:
    """
    Main CLI function
    """
    print("🌟 Welcome to GenAI Foundry CDK CLI 🌟")
    print("=" * 50)
    
    # Get user choice
    choice = questionary.select(
        "Choose a stack to deploy:",
        choices=list(STACKS.keys()) + ["❌ Exit"]
    ).ask()
    
    if choice == "❌ Exit":
        print("👋 Exiting CLI. Bye!")
        sys.exit(0)
    
    # Get deployment confirmation
    if not get_deployment_confirmation(STACKS[choice]):
        print("❌ Deployment cancelled by user.")
        sys.exit(0)
    
    # Deploy the selected stack with selection context
    deploy_stack(STACKS[choice]["stack_name"], STACKS[choice]["selection_id"])

if __name__ == "__main__":

    main()
