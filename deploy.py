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
    "banking": {
        "stack_name": "GenAiFoundryBankingStack",
        "description": "Deploy banking-related infrastructure and services",
        "selection_id": "banking",
        "display_name": "🏦 Banking Stack"
    },
    "insurance": {
        "stack_name": "GenAiFoundryInsuranceStack", 
        "description": "Deploy insurance-related infrastructure and services",
        "selection_id": "insurance",
        "display_name": "🛡️ Insurance Stack"
    },
    "retail": {
        "stack_name": "GenAiFoundryRetailStack",
        "description": "Deploy retail-related infrastructure and services",
        "selection_id": "retail",
        "display_name": "🛍️ Retail Stack"
    },
    "healthcare": {
        "stack_name": "GenAiFoundryHealthcareStack",
        "description": "Deploy healthcare-related infrastructure and services",
        "selection_id": "healthcare",
        "display_name": "🏥 Healthcare Stack"
    },
    "manufacturing": {
        "stack_name": "GenAiFoundryManufacturingStack",
        "description": "Deploy manufacturing-related infrastructure and services",
        "selection_id": "manafacturing",
        "display_name": "🏭 Manufacturing Stack"
    }
}

# Model configuration mapping
MODELS = {
    "amazon": {
        "model_id": "us.amazon.nova-pro-v1:0",
        "display_name": "Amazon Nova"
    },
    "anthropic": {
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "display_name": "Anthropic Claude"
    }
}

def deploy_stack(stack_name: str, selection_id: str, model_selection: str) -> None:
    """
    Deploy the selected CDK stack with selection context and model preference
    """
    print(f"🚀 Deploying stack: {stack_name}")
    print(f"📋 Selection ID: {selection_id}")
    print(f"🤖 Model Selection: {model_selection}")
    print("⏳ This may take several minutes...")
    
    try:
        # Set environment variables for the stack to use
        import os
        os.environ["CDK_STACK_SELECTION"] = selection_id
        os.environ["CDK_MODEL_SELECTION"] = model_selection
        print(f"🔧 Set CDK_STACK_SELECTION={selection_id}")
        print(f"🔧 Set CDK_MODEL_SELECTION={model_selection}")
        
        # Deploy with context
        print("🌐 Deploying to AWS...")
        subprocess.run(f"cdk deploy {stack_name} --require-approval never", shell=True, check=True)
        
        print(f"✅ Stack '{stack_name}' deployed successfully!")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)
    finally:
        # Clean up environment variables
        import os
        if "CDK_STACK_SELECTION" in os.environ:
            del os.environ["CDK_STACK_SELECTION"]
        if "CDK_MODEL_SELECTION" in os.environ:
            del os.environ["CDK_MODEL_SELECTION"]

def get_deployment_confirmation(stack_info: Dict[str, str], model_info: Dict[str, str]) -> bool:
    """
    Get final confirmation before deployment
    """
    print(f"\n📝 Deployment Summary:")
    print(f"   Industry: {stack_info['display_name']}")
    print(f"   Stack: {stack_info['stack_name']}")
    print(f"   Description: {stack_info['description']}")
    print(f"   Model: {model_info['display_name']} ({model_info['model_id']})")
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
    
    # Step 1: Get industry selection
    industry_choice = questionary.select(
        "Which industry do you want to deploy?",
        choices=[
            "🏦 Banking",
            "🛡️ Insurance",
            "🛍️ Retail",
            "🏥 Healthcare",
            "🏭 Manufacturing",
            "❌ Exit"
        ]
    ).ask()
    
    if industry_choice == "❌ Exit":
        print("👋 Exiting CLI. Bye!")
        sys.exit(0)
    
    # Map display name to key
    industry_map = {
        "🏦 Banking": "banking",
        "🛡️ Insurance": "insurance",
        "🛍️ Retail": "retail",
        "🏥 Healthcare": "healthcare",
        "🏭 Manufacturing": "manufacturing"
    }
    
    industry_key = industry_map[industry_choice]
    stack_info = STACKS[industry_key]
    
    # Step 2: Get model selection
    model_choice = questionary.select(
        "Which model do you prefer?",
        choices=["amazon", "anthropic"]
    ).ask()
    
    model_info = MODELS[model_choice]
    
    # Get deployment confirmation
    if not get_deployment_confirmation(stack_info, model_info):
        print("❌ Deployment cancelled by user.")
        sys.exit(0)
    
    # Deploy the selected stack with selection context and model preference
    deploy_stack(stack_info["stack_name"], stack_info["selection_id"], model_choice)

if __name__ == "__main__":

    main()
