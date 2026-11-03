import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import repair_company_homepages as repair
import build_likely_company_homepages as builder
import member_career_search as agent

class RepairTests(unittest.TestCase):
    def test_checkpoint_resume_and_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'companies.csv'
            fields = ['member', 'likely_company_homepage', 'verification', 'custom']
            rows = [{'member': n, 'likely_company_homepage': u, 'verification': '', 'custom': 'keep'} for n,u in [('Existing','https://existing.test/'),('Found',''),('Candidate',''),('Error',''),('Suspicious','')]]
            repair.save(path, fields, rows)
            def lookup(row):
                if row['member'] == 'Suspicious':
                    return {'url':'https://brand-inc.com/','verification':'Found in page content'}, ''
                if row['member'] == 'Found':
                    return {'url':'https://found.test/','verification':'Found in page content'}, ''
                if row['member'] == 'Candidate':
                    return {'url':'https://candidate.test/','verification':'Found in domain'}, ''
                return {'url':'','verification':'Search failed'}, 'timeout'
            args = argparse.Namespace(input=path, limit=0, workers=2, retry_checked=False)
            with patch.object(repair,'repair',side_effect=lookup) as mock:
                repair.run(args)
                self.assertEqual(mock.call_count,4)
            with path.open(encoding='utf-8-sig') as f:
                result=list(csv.DictReader(f))
            self.assertEqual(result[0]['likely_company_homepage'],'https://existing.test/')
            self.assertEqual(result[1]['likely_company_homepage'],'https://found.test/')
            self.assertEqual(result[2]['likely_company_homepage'],'')
            self.assertEqual(result[2]['homepage_candidate'],'https://candidate.test/')
            self.assertEqual(result[3]['homepage_search_error'],'timeout')
            self.assertEqual(result[4]['likely_company_homepage'],'')
            self.assertEqual(result[4]['homepage_candidate'],'https://brand-inc.com/')
            self.assertTrue(all(r['custom']=='keep' for r in result))
            with patch.object(repair,'repair') as mock:
                repair.run(args)
                mock.assert_not_called()

    def test_abbreviated_domain(self):
        search=type('Search',(),{'results':lambda *a,**k:[{'link':'https://brand.test/about','title':'Abipa International','snippet':''}]})()
        with patch.object(agent,'_fetch_page',return_value='<title>Abipa International</title>'):
            self.assertEqual(agent.find_company_website(search,'Abipa International')['url'],'https://brand.test/')

    def test_builder_preserves_repaired_verification(self):
        with tempfile.TemporaryDirectory() as d:
            source,target=Path(d)/'source.csv',Path(d)/'target.csv'
            repair.save(source,['association','member','association_directory_url','member_website','company_website_verification'],[{'association':'A','member':'Brand','company_website_verification':'Search failed'}])
            repair.save(target,['association','member','likely_company_homepage','verification'],[{'association':'A','member':'Brand','likely_company_homepage':'https://brand.test/','verification':'Found in page content'}])
            builder.run(argparse.Namespace(input=source,output=target))
            with target.open(encoding='utf-8-sig') as f:
                self.assertEqual(next(csv.DictReader(f))['verification'],'Found in page content')
