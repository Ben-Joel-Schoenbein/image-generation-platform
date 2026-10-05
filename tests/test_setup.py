import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('image_studio_bootstrap', HERE/'scripts/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
BCRYPT = '$2a$14$' + 'a' * 53


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'.env.example').write_text('DOMAIN=images.example.com\nLIBRARY_DOMAIN=library.example.com\nADMIN_USERNAME=admin\nADMIN_PASSWORD=change-this-password\nSESSION_SECRET=replace-with-secret\nPOSTGRES_USER=image_library\nPOSTGRES_DB=image_library\nPOSTGRES_PASSWORD=replace-with-secret\nLIBRARY_AUTH_USER=friends\nLIBRARY_AUTH_HASH=replace-with-hash\nMEDIA_PROXY_SECRET=replace-with-secret\nDISCORD_TOKEN=your-discord-application-token\nDISCORD_BOT_SECRET=replace-with-secret\n')
        (self.root/'compose.yaml').touch(); (self.root/'compose.heretic.yaml').touch()
        self.args = SimpleNamespace(domain='generator.test', library_domain='archive.test', non_interactive=True)

    def configured(self):
        text = '\n'.join(["DOMAIN='generator.test'", "LIBRARY_DOMAIN='archive.test'", "IMAGE_STUDIO_URL='https://generator.test'", "ADMIN_USERNAME='my-admin'", "ADMIN_PASSWORD='my-existing-admin-password'", "SESSION_SECRET='"+'s'*64+"'", "MEDIA_PROXY_SECRET='"+'m'*64+"'", "POSTGRES_PASSWORD='"+'p'*64+"'", "POSTGRES_DB='my-db'", "POSTGRES_USER='my-user'", "LIBRARY_AUTH_USER='my-friends'", "LIBRARY_AUTH_HASH='"+BCRYPT+"'", "DISCORD_BOT_SECRET='"+'b'*64+"'", "DISCORD_TOKEN='valid-test-token'", "MY_SETTING='preserve-me'", "COMPOSE_FILE='compose.yaml:compose.heretic.yaml'", "COMPOSE_PROFILES='discord'"])+ '\n'
        (self.root/'.env').write_text(text)
        return text

    def new_config(self):
        with patch.object(setup, 'hash_password', return_value=BCRYPT):
            return setup.prepare_env(self.root, self.args, ['docker'])

    def test_generated_secrets_private_and_repeat_preserves_them(self):
        before, after, values, credentials, files = self.new_config()
        self.assertGreaterEqual(len(values['ADMIN_PASSWORD']),14)
        for key in ['SESSION_SECRET','MEDIA_PROXY_SECRET','POSTGRES_PASSWORD','DISCORD_BOT_SECRET']: self.assertEqual(len(values[key]),64)
        self.assertEqual(values['COMPOSE_FILE'],'compose.yaml:compose.heretic.yaml')
        self.assertNotIn('discord',values['COMPOSE_PROFILES'])
        setup.save_configuration(self.root,before,after,credentials)
        self.assertEqual((self.root/'.env').stat().st_mode & 0o777,0o600)
        self.assertEqual((self.root/'setup-credentials.txt').stat().st_mode & 0o777,0o600)
        with patch.object(setup,'hash_password',side_effect=AssertionError('should not re-hash')):
            before2,after2,values2,credentials2,_=setup.prepare_env(self.root,self.args,['docker'])
        self.assertEqual(after2,after)
        self.assertEqual(credentials2,[])
        self.assertEqual(values2,values)

    def test_existing_configuration_and_custom_values_preserved(self):
        original=self.configured()
        before,after,values,credentials,_=setup.prepare_env(self.root,self.args,['docker'])
        self.assertEqual(before,original);self.assertEqual(after,original);self.assertEqual(credentials,[])
        self.assertEqual(values['MY_SETTING'],'preserve-me');self.assertEqual(values['COMPOSE_PROFILES'],'discord')

    def test_override_and_extra_profile_retained(self):
        self.configured()
        text=(self.root/'.env').read_text().replace("COMPOSE_FILE='compose.yaml:compose.heretic.yaml'",'').replace("COMPOSE_PROFILES='discord'","COMPOSE_PROFILES='extra,discord'")
        (self.root/'.env').write_text(text); (self.root/'compose.override.yaml').touch()
        _,after,values,_,files=setup.prepare_env(self.root,self.args,['docker'])
        self.assertEqual(files,['compose.yaml','compose.override.yaml','compose.heretic.yaml'])
        self.assertEqual(values['COMPOSE_PROFILES'],'extra,discord')

    def test_missing_domains_and_same_domains_fail_without_writes(self):
        self.args.domain=None
        with self.assertRaises(ValueError):self.new_config()
        self.assertFalse((self.root/'.env').exists())
        self.args.domain='archive.test'
        with self.assertRaises(ValueError):self.new_config()
        self.assertFalse((self.root/'.env').exists())

    def test_existing_generator_data_requires_original_env(self):
        (self.root/'data').mkdir();(self.root/'data/studio.sqlite3').write_bytes(b'existing-data')
        with self.assertRaisesRegex(ValueError,'original .env'):self.new_config()
        self.assertFalse((self.root/'.env').exists())

    def test_env_changed_during_download_is_not_overwritten(self):
        self.configured()
        before,after,_,credentials,_=self.new_config()
        (self.root/'.env').write_text(before+'LATER_SETTING=keep\n')
        with self.assertRaisesRegex(ValueError,'changed'):setup.save_configuration(self.root,before,after,credentials)
        self.assertIn('LATER_SETTING=keep',(self.root/'.env').read_text())

    def test_env_backup_keeps_old_credentials_and_mode(self):
        old=self.configured()
        setup.save_configuration(self.root,old,old+'NEW_SETTING=1\n',[])
        backup=next(self.root.glob('.env.setup-backup-*'))
        self.assertEqual(backup.read_text(),old);self.assertEqual(backup.stat().st_mode & 0o777,0o600)

    def test_secret_quoting_roundtrip(self):
        values={'SECRET':"literal$2a$'quote",'SPACE':'spaces # kept'}
        self.assertEqual(setup.read_env(setup.update_env('',values)),values)
        with self.assertRaises(ValueError):setup.env_value('newline\nsecret')
        with self.assertRaises(ValueError):setup.read_env('A=1\nA=2\n')

    def item(self,raw,dest='models/tiny.bin',blob=False):
        item={'destination':dest,'repo':'test/repo','revision':'a'*40,'file':'tiny.bin','size':len(raw)}
        if blob:item['git_blob']=hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()
        else:item['sha256']=hashlib.sha256(raw).hexdigest()
        return item

    def test_verified_existing_file_not_downloaded_or_rewritten(self):
        item=self.item(b'valid')
        p=self.root/item['destination'];p.parent.mkdir();p.write_bytes(b'valid')
        with patch.object(setup,'run',side_effect=AssertionError('no download')):setup.download_files(self.root,{'files':[item]})
        self.assertEqual(p.read_bytes(),b'valid')

    def test_resume_and_atomic_finalize(self):
        raw=b'complete download';item=self.item(raw);p=self.root/item['destination'];p.parent.mkdir();part=p.with_name(p.name+'.part');part.write_bytes(raw[:4])
        def download(command):
            self.assertIn('--continue-at',command);self.assertIn('https://huggingface.co/test/repo/resolve/'+'a'*40+'/tiny.bin',command)
            Path(command[command.index('--output')+1]).write_bytes(raw)
        with patch.object(setup,'run',side_effect=download):setup.download_files(self.root,{'files':[item]})
        self.assertEqual(p.read_bytes(),raw);self.assertFalse(part.exists())

    def test_wrong_existing_checksum_never_overwritten(self):
        item=self.item(b'good');p=self.root/item['destination'];p.parent.mkdir();p.write_bytes(b'evil')
        with patch.object(setup,'run',side_effect=AssertionError('no download')):
            with self.assertRaisesRegex(ValueError,'not overwritten'):setup.download_files(self.root,{'files':[item]})
        self.assertEqual(p.read_bytes(),b'evil')

    def test_bad_download_not_promoted(self):
        item=self.item(b'good');p=self.root/item['destination']
        def bad(command):Path(command[command.index('--output')+1]).write_bytes(b'evil')
        with patch.object(setup,'run',side_effect=bad):
            with self.assertRaisesRegex(ValueError,'Checksum mismatch'):setup.download_files(self.root,{'files':[item]})
        self.assertFalse(p.exists());self.assertTrue(p.with_name(p.name+'.part').exists())

    def test_completed_partial_is_verified_without_network(self):
        item=self.item(b'ready',blob=True);p=self.root/item['destination'];p.parent.mkdir();p.with_name(p.name+'.part').write_bytes(b'ready')
        with patch.object(setup,'run',side_effect=AssertionError('no download')):setup.download_files(self.root,{'files':[item]})
        self.assertEqual(p.read_bytes(),b'ready')

    def test_partial_symlink_and_disk_shortage_stop(self):
        item=self.item(b'good');p=self.root/item['destination'];p.parent.mkdir();outside=self.root/'outside';outside.write_bytes(b'keep');p.with_name(p.name+'.part').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink'):setup.download_files(self.root,{'files':[item]})
        p.with_name(p.name+'.part').unlink()
        with patch.object(setup.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError,'free'):setup.download_files(self.root,{'files':[item]})
        self.assertEqual(outside.read_bytes(),b'keep')

    def test_safe_paths_refuse_escape(self):
        for name in ['../outside','/outside']:
            with self.assertRaises(ValueError):setup.safe_path(self.root,name)
        (self.root/'escape').symlink_to(self.root.parent,target_is_directory=True)
        with self.assertRaises(ValueError):setup.safe_path(self.root,'escape/outside')

    def test_prompt_install_is_idempotent_and_keeps_custom_prompt(self):
        for kind in ['t2i','i2i']:
            p=self.root/f'models/prompt_enhancers/{kind}';p.mkdir(parents=True)
            (p/'system_prompt.txt').write_text('rewritten_prompt '+('x'*1100));(p/'LICENSE').write_text('license')
        setup.install_prompts(self.root);setup.install_prompts(self.root)
        target=self.root/'web/heretic_prompts/t2i/system_prompt.txt';target.write_text('my custom prompt')
        with self.assertRaisesRegex(ValueError,'not overwritten'):setup.install_prompts(self.root)
        self.assertEqual(target.read_text(),'my custom prompt')

    def test_caddy_hash_uses_stdin_and_no_password_argument(self):
        def fake(command,**kwargs):
            self.assertNotIn('secret-password',command);self.assertEqual(kwargs['input'],'secret-password')
            return SimpleNamespace(stdout=BCRYPT+'\n')
        with patch.object(setup,'run',side_effect=fake):self.assertEqual(setup.hash_password(['docker'],'secret-password'),BCRYPT)

    def test_compose_validates_mounts_without_printing_secrets(self):
        def result(command,**kwargs):
            env_file=Path(command[command.index('--env-file')+1]);self.assertEqual(env_file.stat().st_mode & 0o777,0o600)
            cfg={'services':{'comfyui':{'volumes':[{'type':'bind','target':'/opt/ComfyUI/models','source':str(self.root/'models')}]},'prompt-enhancer':{'volumes':[{'type':'bind','target':'/models','source':str(self.root/'models/prompt_enhancers')},{'type':'bind','target':'/config/models.ini','source':str(self.root/'prompt-enhancer/models.ini')}]}}}
            return SimpleNamespace(returncode=0,stdout=json.dumps(cfg))
        with patch.object(setup.subprocess,'run',side_effect=result):setup.compose_config(['docker'],self.root,'SECRET=keep\n',['compose.yaml','compose.heretic.yaml'])
        self.assertEqual(list(self.root.glob('.studio-setup-env-*')),[])
        with patch.object(setup.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='SECRET-DO-NOT-PRINT')):
            with self.assertRaisesRegex(ValueError,'failed validation') as error:setup.compose_config(['docker'],self.root,'SECRET=keep\n',['compose.yaml'])
            self.assertNotIn('SECRET-DO-NOT-PRINT',str(error.exception))

    def test_existing_archive_volume_requires_original_database_password(self):
        config={'services':{'media-db':{'volumes':[{'type':'volume','source':'postgres_data','target':'/var/lib/postgresql/data'}]}},'volumes':{'postgres_data':{'name':'friends-image-studio_postgres_data'}}}
        with patch.object(setup,'run',return_value=SimpleNamespace(stdout='friends-image-studio_postgres_data\n')):
            with self.assertRaisesRegex(ValueError,'original .env'):setup.protect_existing_database(['docker'],config,'POSTGRES_PASSWORD=replace-with-secret\n',{'POSTGRES_PASSWORD':'new-secret'})
        with patch.object(setup,'run',return_value=SimpleNamespace(stdout='other_project_postgres_data\n')):
            setup.protect_existing_database(['docker'],config,'POSTGRES_PASSWORD=replace-with-secret\n',{'POSTGRES_PASSWORD':'new-secret'})
        with patch.object(setup,'run',side_effect=AssertionError('password unchanged')):
            setup.protect_existing_database(['docker'],config,'POSTGRES_PASSWORD=keep\n',{'POSTGRES_PASSWORD':'keep'})


if __name__ == '__main__':unittest.main(verbosity=2)
