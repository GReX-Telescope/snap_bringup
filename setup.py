# -*- coding: utf-8 -*-
from setuptools import setup

package_dir = {"": "src"}

packages = ["snap_bringup"]

package_data = {"": ["*"]}

install_requires = ["casperfpga", "loguru"]

entry_points = {"console_scripts": ["snap_bringup = snap_bringup.main:main"]}

setup_kwargs = {
    "name": "snap_bringup",
    "version": "0.1.0",
    "description": "Scripts to startup the SNAP board for GReX",
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
