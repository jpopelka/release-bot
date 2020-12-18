# -*- coding: utf-8 -*-
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Tests bot communication with Github"""
import os
import warnings

import pytest

from release_bot.git import Git
from release_bot.github import Github
from tests.conftest import prepare_conf
from .github_utils import GithubUtils, RELEASE_CONF


@pytest.mark.skipif(not os.environ.get('GITHUB_TOKEN'),
                    reason="missing GITHUB_TOKEN environment variable")
class TestGithub:
    """Tests bot communication with Github"""

    def setup_method(self):
        """ setup any state tied to the execution of the given method in a
        class.  setup_method is invoked for every test method of a class.
        """
        configuration = prepare_conf()

        self.g_utils = GithubUtils()

        self.g_utils.create_repo()
        self.g_utils.setup_repo()

        # set conf
        configuration.repository_name = self.g_utils.repo
        configuration.github_username = self.g_utils.github_user
        configuration.clone_url = f"https://github.com/{self.g_utils.github_user}/{self.g_utils.repo}.git"
        configuration.project = configuration.get_project()

        repo_url = f"https://github.com/{self.g_utils.github_user}/{self.g_utils.repo}"
        git = Git(repo_url, configuration)
        self.github = Github(configuration, git)

    def teardown_method(self):
        """ teardown any state that was previously setup with a setup_method
        call.
        """
        if self.g_utils.repo:
            try:
                self.g_utils.delete_repo()
            except Exception as ex:
                # no need to fail the test, just warn
                warnings.warn(f"Could not delete repository {self.g_utils.repo}: {ex!r}")
        self.g_utils.repo = None

    @pytest.fixture()
    def open_issue(self):
        """Opens issue in a repository"""
        return self.g_utils.open_issue()

    def test_get_file(self):
        """Tests fetching release-conf from Github"""
        assert self.github.get_file("release-conf.yaml") == RELEASE_CONF

    def test_latest_rls_not_existing(self):
        """Tests version number when there is no latest release"""
        assert self.github.latest_release() == '0.0.0'

    def test_branch_exists_true(self):
        """Tests if branch exists"""
        assert self.github.branch_exists('main')

    def test_branch_exists_false(self):
        """Tests if branch doesn't exist"""
        assert not self.github.branch_exists('not-main')
