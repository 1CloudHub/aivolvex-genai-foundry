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
        "description": "Deploy banking-related infrastructure and services"
    },
    "🛡️ Insurance Stack": {
        "stack_name": "GenAiFoundryInsuranceStack", 
        "description": "Deploy insurance-related infrastructure and services"
    }
}

def deploy_stack(stack_name: str) -> None:
    """
    Deploy the selected CDK stack
    """
    print(f"🚀 Deploying stack: {stack_name}")
    print("⏳ This may take several minutes...")
    
    try:
        # First synthesize the app
        print("📝 Synthesizing CloudFormation template...")
        subprocess.run(["cdk", "synth", stack_name], check=True)
        
        # Then deploy
        print("🌐 Deploying to AWS...")
        subprocess.run(
            ["cdk", "deploy", stack_name, "--require-approval", "never"], 
            check=True
        )
        
        print(f"✅ Stack '{stack_name}' deployed successfully!")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)

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
    
    # Deploy the selected stack
    deploy_stack(STACKS[choice]["stack_name"])

if __name__ == "__main__":
    main()
