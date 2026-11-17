# Run read-only with Puppet's bundled Ruby. Vault and override bytes never leave
# this collector; only metadata JSON and file fingerprints/checksums are emitted.
require 'base64'
require 'digest'
require 'json'
require 'time'
require 'timeout'

module FleetrollConfigurationSnapshot
  LIMITS = { 'puppet' => 65_536, 'override' => 1_048_576, 'vault' => 16_777_216 }.freeze

  def self.timestamp
    Time.now.utc.iso8601(6)
  end

  def self.fingerprint(stat)
    { 'device' => stat.dev, 'inode' => stat.ino, 'size' => stat.size,
      'mtime_s' => stat.mtime.to_i, 'mtime_ns' => stat.mtime.nsec,
      'ctime_s' => stat.ctime.to_i, 'ctime_ns' => stat.ctime.nsec,
      'mode' => stat.mode & 0o7777, 'uid' => stat.uid, 'gid' => stat.gid }
  end

  def self.read_component(path, name:)
    checked = timestamp
    result = { 'path' => path, 'observed_at' => nil, 'checked_at' => checked }
    opened = false
    File.open(path, File::RDONLY | File::NONBLOCK) do |file|
      opened = true
      before = file.stat
      return result.merge('status' => 'invalid', 'reason' => 'not_regular_file') unless before.file?
      bytes = file.read(LIMITS.fetch(name) + 1) || ''.b
      if bytes.bytesize > LIMITS.fetch(name)
        return result.merge('status' => 'invalid', 'reason' => 'file_too_large')
      end
      after = file.stat
      if fingerprint(before) != fingerprint(after) || fingerprint(after) != fingerprint(File.stat(path))
        return result.merge('status' => 'invalid', 'reason' => 'file_changed_during_read')
      end
      result.merge!('status' => 'present', 'reason' => nil, 'observed_at' => checked,
                    'sha256' => Digest::SHA256.hexdigest(bytes), 'file' => fingerprint(after))
      result['content_b64'] = Base64.strict_encode64(bytes) if name == 'puppet'
      result
    end
  rescue Errno::ENOENT, Errno::ENOTDIR
    if opened
      result.merge('status' => 'invalid', 'reason' => 'file_changed_during_read')
    elsif File.symlink?(path)
      result.merge('status' => 'unreadable', 'reason' => 'dangling_symlink')
    else
      result.merge('status' => 'absent', 'reason' => nil, 'observed_at' => checked)
    end
  rescue SystemCallError, IOError
    result.merge('status' => 'unreadable', 'reason' => 'file_read_failed')
  end

  def self.collect(paths:, timeout_s: 10, between_reads: nil)
    started = timestamp
    first = {}
    components = {}
    Timeout.timeout(timeout_s) do
      paths.each { |name, path| first[name] = read_component(path, name: name) }
      between_reads.call if between_reads
      # Re-read all sources, not just stat: same-size rewrites must be detected.
      paths.each do |name, path|
        before = first.fetch(name)
        after = read_component(path, name: name)
        comparable = %w[present absent].include?(before['status']) && %w[present absent].include?(after['status'])
        stable = comparable && %w[status sha256 file].all? { |key| before[key] == after[key] }
        if stable
          components[name] = before.merge('consistency' => 'stable', 'confirmed_at' => after['observed_at'])
        elsif comparable || before['reason'] == 'file_changed_during_read' || after['reason'] == 'file_changed_during_read'
          components[name] = before.merge('status' => 'invalid', 'reason' => 'file_changed_during_capture',
                                           'consistency' => 'changed', 'observed_at' => nil)
        else
          error = %w[present absent].include?(before['status']) ? after : before
          components[name] = error.merge('consistency' => 'unknown', 'observed_at' => nil)
        end
      end
    end
    finish(paths, started: started, components: components)
  rescue Timeout::Error
    finish(paths, started: started, components: {}, reason: 'snapshot_timeout')
  end

  def self.finish(paths, started:, components:, reason: nil)
    paths.each do |name, path|
      components[name] ||= { 'path' => path, 'status' => 'not_collected', 'reason' => reason,
                            'observed_at' => nil, 'consistency' => 'unknown' }
    end
    consistent = components.values.all? { |item| item['consistency'] == 'stable' }
    changed = components.values.any? { |item| item['consistency'] == 'changed' }
    { 'schema_version' => 1, 'started_at' => started, 'finished_at' => timestamp,
      'method' => 'double_read_v1', 'consistency' => consistent ? 'stable' : changed ? 'changed' : 'unknown',
      'components' => components }
  end

  def self.for_platform(os)
    root = os == 'Darwin' ? '/opt/puppet_environments' : '/etc/puppet'
    vault = os == 'Darwin' ? '/var/root/vault.yaml' : '/root/vault.yaml'
    collect(paths: { 'puppet' => "#{root}/last_run_metadata.json", 'override' => "#{root}/ronin_settings", 'vault' => vault })
  end
end

puts JSON.generate(FleetrollConfigurationSnapshot.for_platform(ARGV.fetch(0))) if __FILE__ == '-e'
