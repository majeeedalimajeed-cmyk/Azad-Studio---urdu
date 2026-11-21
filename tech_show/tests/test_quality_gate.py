import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from quality_gate import validate

def valid_manifest():
 asset=lambda kind,id:{'type':kind,'source_url':'https://example.com/'+id,'depicts_product':'Example One','license':'CC-BY-4.0','license_url':'https://creativecommons.org/licenses/by/4.0/','author':'Photographer','rights_status':'verified','authenticity':'verified'}
 return {'product_name':'Example One','official_sources':['https://example.com/specs'],'editorial_review':'passed','rights_review':'passed','media_authenticity_review':'passed','media':[asset('photo','a'),asset('photo','b'),asset('video','c')],'videos':{lang:{'duration_seconds':420,'narration_language':lang,'visual_review':'passed','audio_review':'passed'} for lang in ('english','hindi')},'caption':'English: example. Roman Urdu: example.','poster':{'visual_review':'passed'}}
class GateTests(unittest.TestCase):
 def test_approved_example_has_no_failures(self):self.assertEqual(validate(valid_manifest()),[])
 def test_real_video_required(self):
  m=valid_manifest();m['media']=m['media'][:2];self.assertIn('verified_product_video_required',validate(m))
 def test_license_and_mismatch_fail(self):
  m=valid_manifest();m['media'][0]['license']='unlicensed';m['media'][1]['depicts_product']='Other Phone';errors=validate(m);self.assertIn('unapproved_media_license',errors);self.assertIn('media_product_mismatch',errors)
 def test_duplicate_fails(self):
  m=valid_manifest();m['media'][1]['source_url']=m['media'][0]['source_url'];self.assertIn('invalid_or_duplicate_media_source',validate(m))
 def test_duration_reviews_caption_fail(self):
  m=valid_manifest();m['videos']['hindi']['duration_seconds']=10;m['editorial_review']='pending';m['caption']='x'*2300;errors=validate(m);self.assertIn('hindi_duration_out_of_range',errors);self.assertIn('editorial_review_required',errors);self.assertIn('caption_missing_or_too_long',errors)
if __name__=='__main__':unittest.main()
