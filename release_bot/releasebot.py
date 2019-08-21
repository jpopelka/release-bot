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

"""
This module provides functionality for automation of releasing projects
into various downstream services
"""
import logging
import time
from sys import exit

from flask import Flask
from semantic_version import Version
from ogr.abstract import IssueStatus, PRStatus

from release_bot.cli import CLI
from release_bot.configuration import configuration
from release_bot.exceptions import ReleaseException
from release_bot.git import Git
from release_bot.github import Github
from release_bot.new_pr import NewPR
from release_bot.new_release import NewRelease
from release_bot.pypi import PyPi
from release_bot.webhooks import GithubWebhooksHandler
from release_bot.init_repo import Init
from release_bot.utils import (
    process_version_from_title,
    GitService,
    which_service,
    which_username,
)


class ReleaseBot:

    def __init__(self, configuration):
        self.conf = configuration
        self.git = Git(self.conf.clone_url, self.conf)
        self.github = Github(configuration, self.git)
        self.pypi = PyPi(configuration, self.git)
        self.logger = configuration.logger
        self.new_release = NewRelease()
        self.new_pr = NewPR()
        self.project = configuration.project
        self.git_service = which_service(self.project)  # Github/Pagure

    def cleanup(self):
        self.new_release = NewRelease()
        self.new_pr = NewPR()
        self.github.comment = []
        self.git.cleanup()

    @staticmethod
    def create_flask_instance(configuration):
        """Create flask instance for receiving Github webhooks"""
        app = Flask(__name__)
        app.add_url_rule('/webhook-handler/',  # route for github callbacks
                         view_func=GithubWebhooksHandler.as_view('github_webhooks_handler',
                                                                 conf=configuration),
                         methods=['POST', ])
        app.run(host='0.0.0.0', port=8080)

    def load_release_conf(self):
        """
        Updates new_release with latest release-conf.yaml from repository
        :return:
        """
        # load release configuration from release-conf.yaml in repository
        conf = self.github.get_file("release-conf.yaml")
        release_conf = self.conf.load_release_conf(conf)
        setup_cfg = self.github.get_file("setup.cfg")
        self.conf.set_pypi_project(release_conf, setup_cfg)

        self.new_release.update(
            changelog=release_conf.get('changelog'),
            author_name=release_conf.get('author_name'),
            author_email=release_conf.get('author_email'),
            pypi=release_conf.get('pypi'),
            trigger_on_issue=release_conf.get('trigger_on_issue'),
            labels=release_conf.get('labels')
        )

    def find_open_release_issues(self):
        """
        Looks for opened release issues on github
        :return: True on found, False if not found
        """
        release_issues = {}
        latest_version = Version(self.github.latest_release())
        opened_issues = self.project.get_issue_list(IssueStatus.open)
        if not opened_issues:
            self.logger.debug(f'No more open issues found')
        else:
            for issue in opened_issues:
                match, version = process_version_from_title(issue.title, latest_version)
                if match:
                    if self.project.can_close_issue(which_username(self.conf), issue):
                        release_issues[version] = issue
                        self.logger.info(f'Found new release issue with version: {version}')
                    else:
                        self.logger.warning(f"User {which_username(self.conf)} "
                                            f"has no permission to modify issue")

        if len(release_issues) > 1:
            msg = f'Multiple release issues are open {release_issues}, please reduce them to one'
            self.logger.error(msg)
            return False
        if len(release_issues) == 1:
            if which_service(self.project) == GitService.Github:
                labels = self.new_release.labels
            elif which_service(self.project) == GitService.Pagure:
                # Putting labels on Pagure issues is not implemented yet inside ogr-lib
                labels = None

            for version, issue in release_issues.items():
                self.new_pr.update_new_pr_details(
                    version=version,
                    issue_id=None,
                    issue_number=issue.id,
                    labels=labels
                )
                return True
        else:
            return False

    def find_newest_release_pull_request(self):
        """
        Find newest merged release PR

        :return: bool, whether PR was found
        """
        latest_version = Version(self.github.latest_release())
        merged_prs = self.github.walk_through_prs(PRStatus.merged)

        if not merged_prs:
            self.logger.debug(f'No merged release PR found')
            return False

        for merged_pr in merged_prs:
            match, version = process_version_from_title(merged_pr.title, latest_version)
            if match:
                self.logger.info(f"Found merged release PR with version {version}")
                self.new_release.update_pr_details(
                    version=version,
                    commitish='master',
                    pr_id=None,
                    pr_number=merged_pr.id,
                    author_email=None,
                    author_name=merged_pr.author
                )
                return True

    def make_release_pull_request(self):
        """
        Makes release pull request and handles outcome
        :return: whether making PR was successful
        """

        def pr_handler(success):
            """
            Handler for the outcome of making a PR
            :param success: whether making PR was successful
            :return:
            """
            result = 'made' if success else 'failed to make'
            msg = f"I just {result} a PR request for a release version {self.new_pr.version}"
            level = logging.INFO if success else logging.ERROR
            self.logger.log(level, msg)
            if success:
                msg += f"\n Here's a [link to the PR]({self.new_pr.pr_url})"
            comment_backup = self.github.comment.copy()
            self.github.comment = [msg]
            self.project.issue_comment(self.new_pr.issue_number, msg)
            self.github.comment = comment_backup
            if success:
                self.project.issue_close(self.new_pr.issue_number)
                self.logger.debug(f'Closed issue #{self.new_pr.issue_number}')

        latest_gh_str = self.github.latest_release()
        self.new_pr.previous_version = latest_gh_str
        if Version.coerce(latest_gh_str) >= Version.coerce(self.new_pr.version):
            msg = f"Version ({latest_gh_str}) is already released and this issue is ignored."
            self.logger.warning(msg)
            return False
        msg = (f"Making a new PR for release of version "
               f"{self.new_pr.version} based on the issue.")
        if not self.conf.dry_run:
            self.logger.info(msg)

        try:
            self.new_pr.repo = self.git
            if not self.new_pr.repo:
                raise ReleaseException("Couldn't clone repository!")
            if self.github.make_release_pr(self.new_pr, self.conf.gitchangelog):
                pr_handler(success=True)
                return True
        except ReleaseException:
            pr_handler(success=False)
            raise
        return False

    def make_new_github_release(self):
        def release_handler(success):
            result = "released" if success else "failed to release"
            msg = f"I just {result} version {self.new_release.version} on {self.git_service.name}"
            level = logging.INFO if success else logging.ERROR
            self.logger.log(level, msg)
            self.github.comment.append(msg)

        try:
            latest_release = self.github.latest_release()
        except ReleaseException as exc:
            raise ReleaseException(f"Failed getting latest {self.git_service.name} release (zip).\n{exc}")

        if Version.coerce(latest_release) >= Version.coerce(self.new_release.version):
            self.logger.info(
                f"{self.new_release.version} has already been released on {self.git_service.name}")
        else:
            try:
                if self.conf.dry_run:
                    return None
                released, self.new_release = self.github.make_new_release(self.new_release)
                if released:
                    release_handler(success=True)
            except ReleaseException:
                release_handler(success=False)
                raise
        return self.new_release

    def make_new_pypi_release(self):
        if not self.new_release.pypi:
            self.logger.debug('Skipping PyPi release')
            return False

        def release_handler(success):
            result = "released" if success else "failed to release"
            if self.conf.dry_run:
                msg = f"I would have {result} version {self.new_release.version} on PyPI now."
            else:
                msg = f"I just {result} version {self.new_release.version} on PyPI"
            level = logging.INFO if success else logging.ERROR
            self.logger.log(level, msg)
            self.github.comment.append(msg)

        latest_pypi = self.pypi.latest_version()
        if Version.coerce(latest_pypi) >= Version.coerce(self.new_release.version):
            msg = f"{self.conf.pypi_project}-{self.new_release.version} " \
                f"or higher version has already been released on PyPi"
            self.logger.info(msg)
            return False
        self.git.fetch_tags()
        self.git.checkout(self.new_release.version)
        try:
            if self.pypi.release() == False:
                return False
            release_handler(success=True)
        except ReleaseException:
            release_handler(success=False)
            raise
        finally:
            self.git.checkout('master')

        return True

    def run(self):
        self.logger.info(f"release-bot v{configuration.version} reporting for duty!")
        if self.conf.dry_run:
            self.logger.info("Running in dry-run mode.")
        try:
            while True:
                self.git.pull()
                try:
                    self.load_release_conf()
                    if self.find_newest_release_pull_request():
                        self.make_new_github_release()
                        # Try to do PyPi release regardless whether we just did github release
                        # for case that in previous iteration (of the 'while True' loop)
                        # we succeeded with github release, but failed with PyPi release
                        self.make_new_pypi_release()
                except ReleaseException as exc:
                    self.logger.error(exc)

                # Moved out of the previous try-except block, because if it
                # encounters ReleaseException while checking for PyPi sources
                # it doesn't check for GitHub issues.
                try:
                    if self.new_release.trigger_on_issue and self.find_open_release_issues():
                        if self.new_release.labels is not None:
                            self.project.add_issue_labels(self.new_pr.issue_number,
                                                          self.new_release.labels)
                        self.make_release_pull_request()
                except ReleaseException as exc:
                    self.logger.error(exc)

                msg = '\n'.join(self.github.comment)
                self.project.pr_comment(self.new_release.pr_number, msg)
                self.github.comment = []  # clean up

                self.logger.debug(f"Done. Going to sleep for {self.conf.refresh_interval}s")
                time.sleep(self.conf.refresh_interval)
        finally:
            self.cleanup()


def main():
    args = CLI.parse_arguments()
    if args.subcommand == "run_init":
        init_repo = Init()
        with Init() as init_repo:
            init_repo.run(args.silent)
    else:
        CLI.get_configuration(args)
        configuration.load_configuration()
        if configuration.webhook_handler:
            ReleaseBot.create_flask_instance(configuration)
        else:
            rb = ReleaseBot(configuration)
            rb.run()


if __name__ == '__main__':
    exit(main())
