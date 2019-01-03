.. image:: https://dvc.org/static/img/logo-owl-readme.png
  :target: https://dvc.org
  :alt: DVC logo

`Website <https://dvc.org>`_
• `Docs <https://dvc.org/doc>`_
• `Twitter <https://twitter.com/iterativeai>`_
• `Chat (Community & Support) <https://dvc.org/chat>`_
• `Tutorial <https://dvc.org/doc/tutorial>`_
• `Mailing List <https://sweedom.us10.list-manage.com/subscribe/post?u=a08bf93caae4063c4e6a351f6&id=24c0ecc49a>`_

.. image:: https://travis-ci.com/iterative/dvc.svg?branch=master
  :target: https://travis-ci.com/iterative/dvc
  :alt: Travis

.. image:: https://ci.appveyor.com/api/projects/status/github/iterative/dvc?branch=master&svg=true
  :target: https://ci.appveyor.com/project/iterative/dvc/branch/master
  :alt: Windows Build

.. image:: https://codeclimate.com/github/iterative/dvc/badges/gpa.svg
  :target: https://codeclimate.com/github/iterative/dvc
  :alt: Code Climate

.. image:: https://codecov.io/gh/iterative/dvc/branch/master/graph/badge.svg
  :target: https://codecov.io/gh/iterative/dvc
  :alt: Codecov

|

**Data Science Version Control** or **DVC** is an **open-source** tool for data science and
machine learning projects. With a simple and flexible Git-like architecture and interface it
helps data scientists:

#. manage **machine learning models** - versioning, including data sets and transformations (scripts) that were
   used to generate models;

#. make projects **reproducible**;

#. make projects **shareable**;

#. manage experiments with branching and **metrics** tracking;

It aims to replace tools like Excel and Docs that are being commonly used as a knowledge repo and
a ledger for the team, ad-hoc scripts to track and move deploy different model versions, ad-hoc
data file suffixes and prefixes.

.. contents:: **Contents**
  :backlinks: none

How DVC works
=============

DVC is compatible with Git for storing code and the dependency graph (DAG), but not data files cache.
To store and share data files cache DVC supports remotes - any cloud (S3, Azure, Google Cloud, etc) or any on-premise
network storage (via SSH, for example).

.. image:: https://dvc.org/static/img/flow.gif
   :target: https://dvc.org/static/img/flow.gif
   :alt: how_dvc_works

Quick start
===========

Please read `Get Started <https://dvc.org/doc/get-started>`_ for the full version. Common workflow commands include:

+-----------------------------------+-------------------------------------------------------------------+
| Step                              | Command                                                           |
+===================================+===================================================================+
| Track code and data together      | | ``$ git add train.py``                                          |
|                                   | | ``$ dvc add images.zip``                                        |
+-----------------------------------+-------------------------------------------------------------------+
| Connect code and data by commands | | ``$ dvc run -d images.zip -o images/ unzip -q images.zip``      |
|                                   | | ``$ dvc run -d images/ -d train.py -o model.p python train.py`` |
+-----------------------------------+-------------------------------------------------------------------+
| Make changes and reproduce        | | ``$ vi train.py``                                               |
|                                   | | ``$ dvc repro model.p.dvc``                                     |
+-----------------------------------+-------------------------------------------------------------------+
| Share code                        | | ``$ git add .``                                                 |
|                                   | | ``$ git commit -m 'The baseline model'``                        |
|                                   | | ``$ git push``                                                  |
+-----------------------------------+-------------------------------------------------------------------+
| Share data and ML models          | | ``$ dvc remote add myremote s3://mybucket/image_cnn``           |
|                                   | | ``$ dvc config core.remote myremote``                           |
|                                   | | ``$ dvc push``                                                  |
+-----------------------------------+-------------------------------------------------------------------+

Installation
============

There are three options to install DVC: ``pip``, Homebrew, or an OS-specific package:

pip (PyPI)
----------

Stable
^^^^^^
.. code-block:: bash

   pip install dvc

Development
^^^^^^^^^^^
.. code-block:: bash

   pip install git+git://github.com/iterative/dvc

Homebrew
--------

.. code-block:: bash

   brew install iterative/homebrew-dvc/dvc

or:

.. code-block:: bash

   brew cask install iterative/homebrew-dvc/dvc

Package
-------

Self-contained packages for Windows, Linux, Mac are available. The latest version of the packages can be found at
GitHub `releases page <https://github.com/iterative/dvc/releases>`_.

Ubuntu / Debian (apt)
^^^^^^^^^^^^^^^^^^^^^
.. code-block:: bash

   sudo wget https://dvc.org/deb/dvc.list -O etc/apt/sources.list.d/dvc.list
   sudo apt-get update
   sudo apt-get install dvc

Fedora / CentOS (rpm)
^^^^^^^^^^^^^^^^^^^^^
.. code-block:: bash

   sudo wget https://dvc.org/rpm/dvc.repo -O /etc/yum.repos.d/dvc.repo
   sudo yum update
   sudo yum install dvc

Arch linux (AUR)
^^^^^^^^^^^^^^^^
*Unofficial package*, any inquiries regarding the AUR package,
`refer to the maintainer <https://github.com/mroutis/pkgbuilds>`_.

.. code-block:: bash

   yay -S dvc

Related technologies
====================

#. `Git-annex <https://git-annex.branchable.com/>`_ - DVC uses the idea of storing the content of large files (that you
   don't want to see in your Git repository) in a local key-value store and uses file hardlinks/symlinks instead of the
   copying actual files.

#. `Git-LFS <https://git-lfs.github.com/>`_ - DVC is compatible with any remote storage (S3, Google Cloud, Azure, SSH,
   etc). DVC utilizes reflinks or hardlinks to avoid copy operation on checkouts which makes much more efficient for
   large data files.

#. *Makefile* (and its analogues). DVC tracks dependencies (DAG).

#. `Workflow Management Systems <https://en.wikipedia.org/wiki/Workflow_management_system>`_. DVC is a workflow
   management system designed specifically to manage machine learning experiments. DVC is built on top of Git.

Contributing
============
Contributions are welcome! Please see our `Contributing Guide <https://dvc.org/doc/user-guide/contributing/>`_ for more
details.

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/0
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/0
  :alt: 0

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/1
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/1
  :alt: 1

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/2
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/2
  :alt: 2

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/3
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/3
  :alt: 3

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/4
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/4
  :alt: 4

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/5
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/5
  :alt: 5

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/6
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/6
  :alt: 6

.. image:: https://sourcerer.io/fame/efiop/iterative/dvc/images/7
  :target: https://sourcerer.io/fame/efiop/iterative/dvc/links/7
  :alt: 7

Mailing List
============

Want to stay up to date? Want to help improve DVC by participating in our ocassional polls? Subscribe to our `mailing list <https://sweedom.us10.list-manage.com/subscribe/post?u=a08bf93caae4063c4e6a351f6&id=24c0ecc49a>`_. No spam, really low traffic.

Copyright
=========

This project is distributed under the Apache license version 2.0 (see the LICENSE file in the project root).

By submitting a pull request for this project, you agree to license your contribution under the Apache license version
2.0 to this project.
