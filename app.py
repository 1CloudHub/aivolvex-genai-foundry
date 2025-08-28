#!/usr/bin/env python3
"""
GenAI Foundry CDK Application
Main CDK app for deploying Banking and Insurance stacks
Designed to work with deploy.py for automated deployment in CloudShell
"""

import os
import sys
import aws_cdk as cdk
from final_cdk.Banking_cdk_stack import BankingCdkStack
from final_cdk.Insurance_cdk_stack import InsuranceCdkStack

def create_cdk_app():
    """
    Create and configure the CDK application
    Returns the configured CDK app instance
    """
    # Create CDK app instance
    app = cdk.App()
    
    # Create Banking Stack
    BankingCdkStack(
        app,
        "BankingCdkStack"
    )
    
    # Create Insurance Stack
    InsuranceCdkStack(
        app,
        "InsuranceCdkStack"
    )
    
    return app

def main():
    """
    Main function to create and synthesize the CDK application
    This function is called when app.py is run directly
    """
    # Create the CDK app
    app = create_cdk_app()

if __name__ == "__main__":
    main()
