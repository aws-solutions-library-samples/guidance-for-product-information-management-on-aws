#!/usr/bin/env python3
"""
AWS PIM System - Main CDK Application Entry Point
Modular Blueprint Architecture with Add-on Services
"""
import os
import sys

# Add src/ to Python path so imports work when run from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aws_cdk import App, Environment
from pim_system.infrastructure.core_stack import PimCoreStack
from pim_system.config.deployment_config import DeploymentConfig


def main():
    """Main application entry point"""
    app = App()
    
    # Load deployment configuration
    config = DeploymentConfig.load_from_context(app)
    
    # Create environment configuration
    env = Environment(
        account=os.getenv('CDK_DEFAULT_ACCOUNT'),
        region=config.region
    )
    
    # Deploy PIM core stack
    PimCoreStack(
        app,
        "pim-on-aws",
        config=config,
        env=env,
        description=f"PIM Core Blueprint - {config.environment}"
    )
    
    app.synth()


if __name__ == "__main__":
    main()