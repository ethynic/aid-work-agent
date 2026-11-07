"""Search catalog uses local group and user names before any UI action."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'drivers', 'py'))
import wecom_localdb as drv


class DirectorySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = self.temp.name
        self.session = sqlite3.connect(os.path.join(self.dir, 'session.db.dec.db'))
        self.session.execute('CREATE TABLE conversation_table(conversation_id TEXT, name TEXT)')
        self.session.executemany('INSERT INTO conversation_table VALUES (?,?)', [
            ('R:sales', '售前客服群'),
            ('R:talk1', '新劳务云系统项目沟通群'),
            ('R:talk2', '行政合同归档 智能体 沟通'),
            ('R:same1', '同名群'), ('R:same2', '同名群'),
        ])
        self.session.commit()
        user = sqlite3.connect(os.path.join(self.dir, 'user.db.dec.db'))
        user.execute('CREATE TABLE user_table(id INTEGER, name TEXT, alias TEXT, remark TEXT)')
        user.executemany('INSERT INTO user_table VALUES (?,?,?,?)', [
            (100, '本人', '', ''), (101, '孙晨', '', ''), (102, '小明', '', '')])
        user.commit()
        user.close()

    def tearDown(self):
        self.session.close()
        self.temp.cleanup()

    def search(self, query, kind='any'):
        return drv._directory_search(self.dir, self.session, '100',
                                     {'query': query, 'type': kind, 'limit': 20})

    def test_unique_group_and_user_without_conversation(self):
        group = self.search('售前客服群')
        self.assertEqual(group['match_kind'], 'exact')
        self.assertEqual(group['candidates'], [{'name': '售前客服群', 'section': '群聊'}])
        user = self.search('孙晨')
        self.assertEqual(user['match_count'], 1)
        self.assertEqual(user['candidates'], [{'name': '孙晨', 'section': '联系人'}])
        self_chat = self.search('本人')
        self.assertEqual(self_chat['match_count'], 1, '本人会话也是可搜索联系人')

    def test_typo_returns_candidate_but_no_match(self):
        result = self.search('孙程')
        self.assertEqual(result['match_count'], 0)
        self.assertIn({'name': '孙晨', 'section': '联系人'}, result['candidates'])

    def test_distinct_partial_matches_and_identical_names(self):
        result = self.search('沟通', 'group')
        self.assertEqual(result['match_count'], 2)
        self.assertFalse(result['same_name'])
        self.assertEqual({row['name'] for row in result['candidates']},
                         {'新劳务云系统项目沟通群', '行政合同归档 智能体 沟通'})
        same = self.search('同名群')
        self.assertEqual(same['match_count'], 2)
        self.assertTrue(same['same_name'])

    def test_exact_name_wins_over_contains(self):
        self.session.execute("INSERT INTO conversation_table VALUES('R:extra','售前客服群二')")
        self.session.commit()
        self.assertEqual(self.search('售前客服群')['match_count'], 1)

    def test_contact_and_group_with_same_name_require_global_disambiguation(self):
        self.session.execute("INSERT INTO conversation_table VALUES('R:shared','孙晨')")
        self.session.commit()
        self.assertEqual(self.search('孙晨', 'contact')['match_count'], 1)
        self.assertEqual(self.search('孙晨', 'group')['match_count'], 1)
        all_types = self.search('孙晨')
        self.assertEqual(all_types['match_count'], 2)
        self.assertTrue(all_types['same_name'])


if __name__ == '__main__':
    unittest.main()
