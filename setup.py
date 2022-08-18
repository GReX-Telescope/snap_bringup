# -*- coding: utf-8 -*-
from setuptools import setup

package_dir = {"": "src"}

packages = ["snapctl"]

package_data = {"": ["*"]}

install_requires = ["casperfpga", "loguru"]

entry_points = {"console_scripts": ["snapctl = snapctl.main:main"]}

setup_kwargs = {
    "name": "snapctl",
    "version": "0.1.0",
    "description": "Scripts to startup and control the SNAP board for GReX",
    "long_description": None,
    "author": "Kiran Shila",
    "author_email": "me@kiranshila.com",
    "maintainer": None,
    "maintainer_email": None,
    "url": None,
    "package_dir": package_dir,
    "packages": packages,
    "package_data": package_data,
    "install_requires": install_requires,
    "entry_points": entry_points,
    "python_requires": ">=3.9,<3.11",
}


setup(**setup_kwargs)
