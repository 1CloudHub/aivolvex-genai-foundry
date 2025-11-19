#!/usr/bin/env python3
"""
GenAI Foundry CDK Application
Main CDK app for deploying Banking, Insurance, Retail, Healthcare, and Manufacturing stacks
Designed to work with deploy.py for automated deployment in CloudShell
"""

import os
import sys
import aws_cdk as cdk
from final_cdk.Banking_cdk_stack import BankingCdkStack
from final_cdk.Insurance_cdk_stack import InsuranceCdkStack
from final_cdk.Retail_cdk_stack import RetailCdkStack
from final_cdk.Healthcare_cdk_stack import HealthcareCdkStack
from final_cdk.Manufacturing_cdk_stack import ManufacturingCdkStack

def create_cdk_app():
    """
    Create and configure the CDK application
    Returns the configured CDK app instance
    """
    # Create CDK app instance
    app = cdk.App()
    
    # Get the selection from environment variable (set by deploy.py)
    stack_selection = os.getenv('CDK_STACK_SELECTION', 'unknown')
    model_selection = os.getenv('CDK_MODEL_SELECTION', 'amazon')  # Default to amazon if not set
    
    # Map model selection to model ID
    model_id_map = {
        'amazon': 'us.amazon.nova-pro-v1:0',
        'anthropic': 'anthropic.claude-3-5-sonnet-20241022-v2:0'
    }
    chat_tool_model = model_id_map.get(model_selection, 'us.amazon.nova-pro-v1:0')
    
    print(f"🔧 CDK Stack Selection: {stack_selection}")
    print(f"🤖 Model Selection: {model_selection}")
    print(f"🔧 Chat Tool Model: {chat_tool_model}")
    
    # Create Banking Stack
    BankingCdkStack(
        app,
        "GenAiFoundryBankingStack",
        stack_selection=stack_selection,  # Pass selection to stack
        chat_tool_model=chat_tool_model,  # Pass model ID to stack
        env=cdk.Environment(
            account=os.getenv('CDK_DEFAULT_ACCOUNT'),
            region=os.getenv('CDK_DEFAULT_REGION')
        )
    )
    
    # Create Insurance Stack
    InsuranceCdkStack(
        app,
        "GenAiFoundryInsuranceStack",
        stack_selection=stack_selection,  # Pass selection to stack
        chat_tool_model=chat_tool_model,  # Pass model ID to stack
        env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION'))
    )
    
    # Create Retail Stack
    RetailCdkStack(
        app,
        "GenAiFoundryRetailStack",
        stack_selection=stack_selection,  # Pass selection to stack
        chat_tool_model=chat_tool_model,  # Pass model ID to stack
        env=cdk.Environment(
            account=os.getenv('CDK_DEFAULT_ACCOUNT'),
            region=os.getenv('CDK_DEFAULT_REGION')
        )
    )

    # Create Healthcare Stack
    HealthcareCdkStack(
        app,
        "GenAiFoundryHealthcareStack",
        stack_selection=stack_selection,  # Pass selection to stack
        chat_tool_model=chat_tool_model,  # Pass model ID to stack
        env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION'))
    )

    # Create Manufacturing Stack
    ManufacturingCdkStack(
        app,
        "GenAiFoundryManufacturingStack",
        stack_selection=stack_selection,  # Pass selection to stack
        chat_tool_model=chat_tool_model,  # Pass model ID to stack
        env=cdk.Environment(
            account=os.getenv('CDK_DEFAULT_ACCOUNT'),
            region=os.getenv('CDK_DEFAULT_REGION')
        )
    )
    return app

def main():
    """
    Main function to create and synthesize the CDK application
    This function is called when app.py is run directly
    """
    # Create the CDK app
    app = create_cdk_app()

    app.synth()

if __name__ == "__main__":
    main()
