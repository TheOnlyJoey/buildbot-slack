# Based on the gitlab reporter from buildbot
from __future__ import absolute_import
from __future__ import print_function

from buildbot.process.results import statusToString
from buildbot.reporters import utils
from buildbot.reporters.base import ReporterBase
from buildbot.reporters.generators.build import BuildStatusGenerator
from buildbot.reporters.message import MessageFormatterFunction
from buildbot.util import httpclientservice
from buildbot.util.httpclientservice import HTTPSession
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
        blocks=True,
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
        self.blocks = blocks

    def _create_default_generators(self):
        formatter = MessageFormatterFunction(lambda context: context['build'], 'json')

        return [
            BuildStatusGenerator(message_formatter=formatter, report_new=True)
        ]

    def getBlocks(self, build, text):
        blocks = []
        fields = []

        fields.append(
            {
                "type": "mrkdwn",
                "text": "*Worker*"
            }
        )

        fields.append(
            {
                "type": "mrkdwn",
                "text": "*Build Reason*"
            }
        )

        fields.append(
            {
                "type": "plain_text",
                "text": build['properties']['workername'][0]
            }
        )

        fields.append(
            {
                "type": "plain_text",
                "text": build['properties'].get('reason', ("(unknown)",))[0]
            }
        )

        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
                "fields": fields
            }
        )

        return blocks

    @defer.inlineCallbacks
    def getBuildDetailsAndSendMessage(self, report):
        build = report["builds"][0]
        text = yield self.getMessage(report)

        postData = {}
        if self.blocks:
            blocks = yield self.getBlocks(build, text)
            if blocks:
                postData["blocks"] = blocks

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
            s = HTTPSession(self.master.httpservice, self.endpoint)
            response = yield s.post("", json=postData)
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
