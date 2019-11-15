from track.client import TrackClient
from tests.config import Remove


def test_trial(file='test.json'):

    with Remove(file):
        client = TrackClient(f'file:{file}')

        client.set_project(name='ConvnetTest', description='Trail test example')
        client.set_group(name='test_group')

        logger1 = client.new_trial()
        client.get_arguments({'a': 1})

        uid1 = logger1.trial.hash

        logger2 = client.new_trial(force=True)
        client.get_arguments({'a': 2})

        uid2 = logger2.trial.hash
        assert uid1 != uid2, 'Trials with different parameters must have different hash'


if __name__ == '__main__':
    test_trial()
