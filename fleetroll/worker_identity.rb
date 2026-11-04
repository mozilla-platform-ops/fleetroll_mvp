# Run remotely with Puppet's bundled Ruby. Only identity fields leave the host.
require 'json'
require 'yaml'
require 'shellwords'
require 'open3'

module FleetrollWorkerIdentity
  KEYS = %w[provisionerId workerType workerGroup workerId].freeze

  def self.worker_index(args)
    # Match the executable (or a shell's script), never arbitrary arguments:
    # `logger -t generic-worker` is not a worker process.
    index = %w[sh bash dash].include?(File.basename(args.first || '')) ? 1 : 0
    return nil unless args[index]

    index if File.basename(args[index]).match?(/\A(?:generic-worker(?:-simple|-multiuser)?|start-worker|run-generic-worker\.sh|run-start-worker\.sh|worker-runner\.sh)\z/)
  end

  def self.config_from_args(args)
    # Wrappers pass their arguments directly to start-worker/generic-worker.
    index = worker_index(args)
    return nil unless index

    tail = args[(index + 1)..]
    flag = tail.index { |arg| %w[--config -config].include?(arg) }
    path = flag ? tail[flag + 1] : tail.find { |arg| arg.start_with?('--config=', '-config=') }&.split('=', 2)&.last
    if !path && File.basename(args[index]).match?(/start-worker|worker-runner/)
      path = tail.find { |arg| arg.start_with?('/') }
    end
    # Relative config paths need a working directory; do not guess.
    path if path&.start_with?('/')
  end

  def self.extract(config)
    return nil unless config.is_a?(Hash)

    provider = config['provider']
    if provider.is_a?(Hash) && provider['workerPoolID'].is_a?(String)
      provisioner, type = provider['workerPoolID'].split('/', 2)
      identity = {
        'provisionerId' => provisioner, 'workerType' => type,
        'workerGroup' => provider['workerGroup'], 'workerId' => provider['workerID']
      }
    else
      identity = config.select { |key, _| KEYS.include?(key) }
    end
    return nil unless KEYS.all? { |key| identity[key].is_a?(String) && identity[key].match?(/\A[a-zA-Z0-9_-]+\z/) }

    identity
  end

  def self.collect(os:, commands:, plist_paths: nil)
    unsupported = false
    paths = commands.filter_map do |command|
      begin
        args = Shellwords.split(command)
        path = config_from_args(args)
        # A running worker with an unresolvable config must not fall back to
        # a possibly stale conventional path.
        index = worker_index(args)
        if !path && index && File.basename(args[index]).match?(/\A(?:generic-worker(?:-simple|-multiuser)?|start-worker)\z/)
          unsupported = true
        end
        path
      rescue ArgumentError
        nil
      end
    end.uniq
    return { 'status' => 'unsupported_launch_config' } if unsupported

    if paths.empty? && os == 'Darwin'
      plists = plist_paths || ([
        '/Library/LaunchAgents/net.generic.worker.plist',
        '/Library/LaunchDaemons/com.mozilla.genericworker.plist',
        '/Library/LaunchDaemons/org.mozilla.worker-runner.plist'
      ] + Dir.glob('/Users/*/Library/LaunchAgents/org.mozilla.worker-runner.plist'))
      paths = plists.filter_map do |plist|
        next unless File.file?(plist)

        output, _, status = Open3.capture3('/usr/bin/plutil', '-extract', 'ProgramArguments', 'json', '-o', '-', plist)
        return { 'status' => 'invalid_launch_config' } unless status.success?

        config_from_args(JSON.parse(output))
      end.uniq
    elsif paths.empty?
      # The managed Linux desktop launcher passes this path to start-worker.
      paths = ['/etc/start-worker.yml'].select { |path| File.file?(path) }
    end

    return { 'status' => 'missing' } if paths.empty?

    # Running runner and generic-worker may refer to two files. Verify they
    # agree instead of arbitrarily picking one (or a stale leftover config).
    identities = paths.map do |path|
      text = File.read(path)
      config = begin
        JSON.parse(text)
      rescue JSON::ParserError
        YAML.safe_load(text)
      end
      extract(config)
    end
    return { 'status' => 'invalid' } if identities.any?(&:nil?)
    return { 'status' => 'ambiguous' } unless identities.uniq.length == 1

    { 'status' => 'ok', 'identity' => identities.first }
  rescue Errno::ENOENT
    { 'status' => 'missing' }
  rescue Errno::EACCES
    { 'status' => 'unreadable' }
  rescue StandardError
    # Parser exceptions can contain config content, including credentials.
    { 'status' => 'invalid' }
  end
end

if $PROGRAM_NAME == __FILE__
  commands, _, status = Open3.capture3('/bin/ps', '-axo', 'command=')
  result = if status.success?
    FleetrollWorkerIdentity.collect(os: ARGV.fetch(0), commands: commands.lines)
  else
    { 'status' => 'process_lookup_failed' }
  end
  puts JSON.generate(result)
end
