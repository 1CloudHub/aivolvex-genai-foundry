#!/usr/bin/env python3
import os
import sys
import aws_cdk as cdk
from final_cdk.Banking_cdk_stack import BankingCdkStack
from final_cdk.Insurance_cdk_stack import InsuranceCdkStack

def get_user_choice():
    """
    Interactive function to get user's stack choice
    Similar to create-vite style prompts
    """
    print("🚀 Welcome to GenAI Foundry CDK Deployment!")
    print("=" * 50)
    print("Please select which stack you would like to deploy:")
    print()
    print("1. 🏦 Banking Stack")
    print()
    print("2. 🛡️ Insurance Stack")
    print()
    print("3. ❌ Cancel deployment")
    print()
    
    while True:
        try:
            choice = input("Enter your choice (1, 2, or 3): ").strip()
            
            if choice == "1":
                return "banking"
            elif choice == "2":
                return "insurance"
            elif choice == "3":
                print("❌ Deployment cancelled by user.")
                sys.exit(0)
            else:
                print("❌ Invalid choice. Please enter 1, 2, or 3.")
        except KeyboardInterrupt:
            print("\n❌ Deployment cancelled by user.")
            sys.exit(0)

def get_deployment_confirmation(stack_name):
    """
    Get final confirmation before deployment
    """
    print(f"\n📋 Deployment Summary:")
    print(f"   Stack: {stack_name}")
    print(f"   Region: {os.getenv('CDK_DEFAULT_REGION', 'Not set')}")
    print(f"   Account: {os.getenv('CDK_DEFAULT_ACCOUNT', 'Not set')}")
    print()
    print("⚠️  This will create AWS resources that may incur costs.")
    print()
    
    while True:
        try:
            confirm = input("Do you want to proceed with deployment? (y/N): ").strip().lower()
            
            if confirm in ['y', 'yes']:
                return True
            elif confirm in ['n', 'no', '']:
                print("❌ Deployment cancelled by user.")
                return False
            else:
                print("❌ Please enter 'y' for yes or 'n' for no.")
        except KeyboardInterrupt:
            print("\n❌ Deployment cancelled by user.")
            return False

def main():
    """
    Main function to handle interactive deployment
    """
    # Check if running in non-interactive mode (for CI/CD)
    if len(sys.argv) > 1 and sys.argv[1] in ['--banking', '--insurance']:
        choice = sys.argv[1].replace('--', '')
        print(f"🚀 Non-interactive mode: Deploying {choice} stack")
    else:
        # Interactive mode
        choice = get_user_choice()
    
    # Get deployment confirmation
    if not get_deployment_confirmation(choice):
        sys.exit(0)
    
    # Create CDK app
    app = cdk.App()
    
    # Common environment configuration
    env = cdk.Environment(
        account=os.getenv('CDK_DEFAULT_ACCOUNT'), 
        region=os.getenv('CDK_DEFAULT_REGION')
    )
    
    # Deploy selected stack
    if choice == "banking":
        print("🏦 Creating Banking Stack...")
        BankingCdkStack(
            app, 
            "GenAiFoundryBankingStack",
            env=env
        )
        print("✅ Banking Stack created successfully!")
        print("📝 To deploy: cdk deploy GenAiFoundryBankingStack")
        
    elif choice == "insurance":
        print("🛡️ Creating Insurance Stack...")
        InsuranceCdkStack(
            app, 
            "GenAiFoundryInsuranceStack",
            env=env
        )
        print("✅ Insurance Stack created successfully!")
        print("📝 To deploy: cdk deploy GenAiFoundryInsuranceStack")
    
    # Synthesize the app
    print("\n🔧 Synthesizing CloudFormation template...")
    app.synth()
    print("✅ Template synthesis completed!")
    print("\n🎉 Ready for deployment!")

if __name__ == "__main__":
    main()
