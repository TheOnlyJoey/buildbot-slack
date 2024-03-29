# Based on the gitlab reporter from buildbot
from __future__ import absolute_import
from __future__ import print_function

from buildbot.process.results import statusToString
from buildbot.reporters import utils
from buildbot.reporters.base import ReporterBase
from buildbot.reporters.generators.build import BuildStatusGenerator
from buildbot.reporters.message import MessageFormatterFunction
from buildbot.util import httpclientservice
from twisted.internet import defer
from twisted.logger import Logger

logger = Logger()

STATUS_EMOJIS = {
    "success": ":relaxed:",
    "warnings": ":point_up:",
    "failure": ":skull_and_crossbones:",
    "skipped": ":point_up:",
    "exception": ":point_up:",
    "retry": ":point_up:",
    "cancelled": ":point_up:",
}

STATUS_COLORS = {
    "success": "#36a64f",
    "warnings": "#fc8c03",
    "failure": "#fc0303",
    "skipped": "#fc8c03",
    "exception": "#fc0303",
    "retry": "#fc8c03",
    "cancelled": "#fc8c03",
}


class SlackStatusPush(ReporterBase):
    name = "SlackStatusPush"
    neededDetails = dict(wantProperties=True)

    def checkConfig(self, endpoint, **kwargs):
        if not isinstance(endpoint, str):
            logger.warning(
                "[SlackStatusPush] endpoint should be a string, got '%s' instead",
                type(endpoint).__name__,
            )
        elif not endpoint.startswith("http"):
            logger.warning(
                '[SlackStatusPush] endpoint should start with "http...", endpoint: %s',
                endpoint,
            )

    @defer.inlineCallbacks
    def reconfigService(
        self,
        endpoint,
        attachments=True,
        commitersInAttachments=False,
        repositoryInAttachments=False,
        generators=None,
        debug=None,
        verify=None,
        **kwargs,
    ):
        self.debug = debug
        self.verify = verify

        if generators is None:
            generators = self._create_default_generators()

        yield super().reconfigService(generators=generators, **kwargs)

        self.endpoint = endpoint
        self.attachments = attachments
        self.commitersInAttachments = commitersInAttachments
        self.repositoryInAttachments = repositoryInAttachments
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.master,
            self.endpoint,
            debug=self.debug,
            verify=self.verify,
        )

    def _create_default_generators(self):
        formatter = MessageFormatterFunction(lambda context: context['build'], 'json')

        return [
            BuildStatusGenerator(message_formatter=formatter, report_new=True)
        ]

    @defer.inlineCallbacks
    def getAttachments(self, build):
        sourcestamps = build["buildset"]["sourcestamps"]
        attachments = []

        for sourcestamp in sourcestamps:
            sha = sourcestamp["revision"]

            title = "Build #{buildid}".format(buildid=build["buildid"])

            project = sourcestamp["project"]
            if project:
                title += " for {project} {sha}".format(project=project, sha=sha)

            sub_build = bool(build["buildset"]["parent_buildid"])
            if sub_build:
                title += " {relationship}: #{parent_build_id}".format(
                    relationship=build["buildset"]["parent_relationship"],
                    parent_build_id=build["buildset"]["parent_buildid"],
                )

            fields = []
            if not sub_build:
                branch = sourcestamp["branch"]
                if branch:
                    fields.append({"title": "Branch", "value": branch, "short": True})

                if self.repositoryInAttachments:
                    repository = sourcestamp["repository"]
                    if repository:
                        fields.append(
                            {
                                "title": "Repository",
                                "value": repository,
                                "short": True
                            }
                        )

                if self.commitersInAttachments:
                    users = yield utils.getResponsibleUsersForBuild(self.master, build["buildid"])
                    if users:
                        fields.append(
                            {
                                "title": "Committers",
                                "value": ", ".join(users),
                                "short": True
                            }
                        )

                builder_name = build["builder"]["name"]
                fields.append(
                    {
                        "title": "Builder",
                        "value": builder_name,
                        "short": True
                    }
                )

            attachments.append(
                {
                    "title": title,
                    "title_link": build["url"],
                    "fallback": "{}: <{}>".format(title, build["url"]),
                    "text": "Status: *{status}*".format(status=statusToString(build["results"])),
                    "color": STATUS_COLORS.get(statusToString(build["results"]), ""),
                    "mrkdwn_in": ["text", "title", "fallback"],
                    "fields": fields,
                }
            )

        return attachments

    @defer.inlineCallbacks
    def getBuildDetailsAndSendMessage(self, report):
        build = report["builds"][0]
        text = yield self.getMessage(report)

        postData = {}
        if self.attachments:
            attachments = yield self.getAttachments(build)
            if attachments:
                postData["attachments"] = attachments

        postData["text"] = text

        return postData

    def getMessage(self, report):
        build = report["builds"][0]
        emoji = STATUS_EMOJIS.get(statusToString(build["results"]), ":hourglass_flowing_sand:")
        url = build["url"]
        buildername = build['properties']['buildername'][0]
        state = build['state_string']

        return f"{emoji} <{url}|{buildername}> - {state}"

    @defer.inlineCallbacks
    def sendMessage(self, reports):
        # We only use the first report, even if multiple are passed
        report = reports[0]
        print(report)

        postData = yield self.getBuildDetailsAndSendMessage(report)
        if not postData:
            return

        logger.info("posting to {url}", url=self.endpoint)
        try:
            print(postData)
            response = yield self._http.post("", json=postData)
            if response.code != 200:
                content = yield response.content()
                logger.error(
                    "{code}: unable to upload status: {content}",
                    code=response.code,
                    content=content,
                )
        except Exception as e:
            logger.error(
                "Failed to send status: {error}",
                error=e,
            )
