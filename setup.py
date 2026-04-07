"""
Setup configuration for AWS PIM System
"""
from setuptools import setup, find_packages

setup(
    name="aws-pim-system",
    version="0.1.0",
    description="AWS Product Information Management System",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="PIM System Team",
    packages=find_packages(where="source"),
    package_dir={"": "source"},
    python_requires=">=3.8",
    install_requires=[
        "aws-cdk-lib>=2.100.0",
        "constructs>=10.0.0",
        "boto3>=1.28.0",
        "pydantic>=2.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.5.0",
        ]
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)