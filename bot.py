import re
import time
from slackclient import SlackClient
import requests
import gitlab


class GitRepo(object):
    def commit_info(self, ref):
        pass

    def update_tag(self, name, ref):
        pass


class GitlabRepo(GitRepo):
    def __init__(self, url, token, repo):
        self.gl = gitlab.Gitlab(url, token)
        self.project = self.gl.projects.get(repo)

    def commit_info(self, ref):
        # TODO need about who submitted?
        commit = self.project.commits.get(ref)
        return dict(sha=commit.id,
                    message=commit.message,
                    author='%s <%s>' % (commit.author_name, commit.author_email))

    def update_tag(self, name, ref):
        try:
            self.project.tags.delete(name)
        except gitlab.exceptions.GitlabDeleteError:
            pass
        self.project.tags.create(dict(tag_name=name, ref=ref))


class SlackChannel(object):
    def __init__(self, nickname, token):
        self.client = SlackClient(token)
        self.nickname = nickname
        self.handles = []
        self.uptime = time.time()

    def connect(self):
        return self.client.rtm_connect()

    def bind_bot_info(self):
        users = self.client.api_call('users.list')
        if not users.get('ok'):
            self.userid = 'unknwon'
        else:
            for user in users.get('members'):
                if 'name' in user and user.get('name') == self.nickname:
                    self.userid = user.get('id')
                    break
        self.call_signs = [self.nickname, '<@' + self.userid + '>']
        #print self.call_signs

    def mainloop(self):
        if not self.connect():
            # TODO throw
            return False
        self.bind_bot_info()
        while True:
            for chan, sender, payload in self.parse_data(self.client.rtm_read()):
                if not chan or not payload:
                    continue
                self.do_handles(chan, sender, *payload)
            time.sleep(0.5)

    def parse_data(self, datas):
        if not datas or not len(datas):
            raise StopIteration
        for data in datas:
            if not data.get('type'):
                continue
            if data['type'] == 'hello':
                print 'bot started'
                continue
            # only allow message data
            if data['type'] != 'message':
                continue
            if not data or not data.get('text') or not data.get('user'):
                continue
            # ignore own message
            if data['user'] == self.userid:
                continue
            # ignore old request message
            if data['ts'] and float(data['ts']) < self.uptime:
                continue
            payload = filter(lambda x: x,
                             map(lambda x: x.strip(),
                                 data['text'].split(' ')))
            if payload[0] not in self.call_signs:
                continue
            yield data['channel'], data['user'], payload[1:]

    def do_handles(self, chan, sender, *payload):
        for handle in self.handles:
            handle(self, chan, sender, payload)

    def add_handle(self, handle):
        self.handles.append(handle)

    def send(self, channel, sender, message):
        slack.client.rtm_send_message(channel=channel,
                                      message='<@%s> %s' % (sender, message))


class Command(object):
    def __init__(self, name, length=1):
        self.name = name
        self.arg_length = length

    def __call__(self, slack, chan, sender, payload):
        pass

    def valid_payload(self, payload):
        if len(payload) != self.arg_length:
            return False
        if payload[0] != self.name:
            return False
        return True


class PingCommand(Command):
    def __init__(self):
        # ping
        super(PingCommand, self).__init__('ping', 1)

    def __call__(self, slack, chan, sender, payload):
        if not self.valid_payload(payload):
            return
        slack.send(chan, sender, 'pong')


class TagCommand(Command):
    def __init__(self):
        # tag repo name ref
        super(TagCommand, self).__init__('tag', 4)

    def __call__(self, slack, chan, sender, payload):
        if not self.valid_payload(payload):
            return
        try:
            git = GitlabRepo(config['GITLAB']['HOST'], config['GITLAB']['TOKEN'],
                             payload[1])
        except gitlab.exceptions.GitlabGetError:
            slack.send(chan, sender, 'unknown repo')
            return
        try:
            # update_tag(name, ref)
            git.update_tag(payload[2], payload[3])
        except:
            slack.send(chan, sender, 'unknown error from server')
            return
        slack.send(chan, sender, 'create tag %s as %s' % (payload[2], payload[3]))


class CommitInfoCommand(Command):
    def __init__(self):
        # ref repo ref
        super(CommitInfoCommand, self).__init__('commit', 3)

    def __call__(self, slack, chan, sender, payload):
        if not self.valid_payload(payload):
            return
        try:
            git = GitlabRepo(config['GITLAB']['HOST'], config['GITLAB']['TOKEN'],
                             payload[1])
        except gitlab.exceptions.GitlabGetError:
            slack.send(chan, sender, 'unknown repo')
            return
        try:
            info = git.commit_info(payload[2])
        except:
            slack.send(chan, sender, 'unknown ref')
            return
        message = '\ncommit {sha}\nAuthor: {author}\n\n{message}'''.format(**info)
        slack.send(chan, sender, message)


if __name__ == '__main__':
    import yaml

    config = yaml.safe_load(open('config.yml'))

    slack = SlackChannel(config['SLACK']['NAME'], config['SLACK']['TOKEN'])

    slack.add_handle(TagCommand())
    slack.add_handle(CommitInfoCommand())
    slack.add_handle(PingCommand())

    slack.mainloop()
