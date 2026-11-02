function result = portal_simulation(action, configuration)
% Local website bridge. The source SLX file is never saved or edited.
persistent sm mdl startTime stopTime cacheConfig waveformDir scopeRecords warningConfig
result = '';
switch action
    case 'prepare'
        if ~isempty(sm) && ~strcmpi(string(sm.Status), 'inactive')
            error('Portal:Busy', 'A portal simulation is already active.');
        end
        sm = [];
        cfg = jsondecode(configuration);
        warningConfig = warning;
        warning('off', 'all');
        cacheConfig = Simulink.fileGenControl('getConfig');
        Simulink.fileGenControl('set', 'CacheFolder', cfg.cache_dir, ...
            'CodeGenFolder', fullfile(cfg.cache_dir, 'codegen'), 'createDir', true);
        for k = 1:numel(cfg.dependency_dirs)
            addpath(cfg.dependency_dirs{k});
        end
        [modelDir, mdl] = fileparts(cfg.model_path);
        addpath(modelDir);
        addpath(cfg.source_dir);
        % Open only for visualization; simulation configuration uses SimulationInput.
        open_system(cfg.model_path);
        if ~isempty(cfg.ann_code_path)
            chart = find(sfroot, '-isa', 'Stateflow.EMChart', 'Path', [mdl '/MATLAB Function']);
            if numel(chart) ~= 1
                error('Portal:Function', 'Expected one ANN MATLAB Function block.');
            end
            chart.Script = fileread(cfg.ann_code_path);
        end
        scopes = struct('id', {}, 'name', {});
        scopeNames = {cfg.scopes.name};
        scopeIds = {cfg.scopes.id};
        for k = 1:numel(scopeNames)
            found = find_system(mdl, 'LookUnderMasks', 'all', ...
                'BlockType', 'Scope', 'Name', scopeNames{k});
            if numel(found) == 1
                scopeConfig = get_param(found{1}, 'ScopeConfiguration');
                publicProperties = properties(scopeConfig);
                required = {'DataLogging','DataLoggingVariableName','DataLoggingSaveFormat', ...
                    'DataLoggingLimitDataPoints','DataLoggingDecimateData','OpenAtSimulationStart'};
                if ~all(ismember(required, publicProperties))
                    error('Portal:ScopeLogging', 'Public scope logging properties are unavailable.');
                end
                scopeConfig.DataLogging = true;
                scopeConfig.DataLoggingVariableName = ['portal_scope_' scopeIds{k}];
                scopeConfig.DataLoggingSaveFormat = 'Dataset';
                scopeConfig.DataLoggingLimitDataPoints = false;
                scopeConfig.DataLoggingDecimateData = false;
                scopeConfig.OpenAtSimulationStart = false;
                scopes(end+1) = struct('id', scopeIds{k}, 'name', scopeNames{k}); %#ok<AGROW>
            end
        end
        pendingDiagram = [cfg.diagram_path '.pending.png'];
        print(['-s' mdl], '-dpng', '-r120', pendingDiagram);
        % Decode the entire PNG before publishing it. Readers never see an
        % image while print() is writing scan lines into it.
        imread(pendingDiagram);
        movefile(pendingDiagram, cfg.diagram_path, 'f');
        startTime = slResolve(get_param(mdl, 'StartTime'), mdl);
        stopTime = slResolve(get_param(mdl, 'StopTime'), mdl);
        in = Simulink.SimulationInput(mdl);
        names = fieldnames(cfg.parameters);
        workspace = get_param(mdl, 'ModelWorkspace');
        for k = 1:numel(names)
            name = names{k};
            if hasVariable(workspace, name)
                in = in.setVariable(name, cfg.parameters.(name), 'Workspace', mdl);
            else
                in = in.setVariable(name, cfg.parameters.(name));
            end
        end
        for k = 1:numel(cfg.block_parameters)
            item = cfg.block_parameters(k);
            blockPath = Simulink.ID.getFullName([mdl ':' item.sid]);
            in = in.setBlockParameter(blockPath, item.parameter, ...
                sprintf('%.17g', cfg.parameters.(item.variable)));
        end
        if ~isempty(cfg.duration)
            validateattributes(cfg.duration, {'double'}, {'scalar','finite','positive'});
            stopTime = startTime + cfg.duration;
            validateattributes(stopTime, {'double'}, {'scalar','finite','>',startTime});
            in = in.setModelParameter('StopTime', sprintf('%.17g', stopTime));
        end
        sm = simulation(in);
        waveformDir = cfg.waveform_dir;
        scopeRecords = scopes;
        result = jsonencode(struct('start_time', startTime, 'stop_time', stopTime, 'scopes', {scopes}));
    case 'export_waveforms'
        result = portal_export_waveforms(sm.SimulationOutput, waveformDir, scopeRecords);
    case 'open_scope'
        cfg = jsondecode(configuration);
        if ~bdIsLoaded(cfg.model)
            error('Portal:Scope', 'The simulation model is no longer loaded.');
        end
        found = find_system(cfg.model, 'LookUnderMasks', 'all', ...
            'BlockType', 'Scope', 'Name', cfg.name);
        if numel(found) ~= 1
            error('Portal:Scope', 'Expected one scope with the requested name.');
        end
        open_system(found{1});
        result = jsonencode(struct('opened', true));
    case 'start'
        start(sm);
    case 'status'
        current = double(sm.Time);
        state = lower(char(sm.Status));
        diagnostic = '';
        if strcmp(state, 'inactive')
            out = sm.SimulationOutput;
            execution = out.SimulationMetadata.ExecutionInfo;
            assignin('base', 'portal_last_simulation_output', out);
            % Simulation.Time can reset when the asynchronous run becomes
            % inactive. The output metadata records the actual final time.
            if isfield(execution, 'StopEventTime')
                current = double(execution.StopEventTime);
            elseif isfield(out.SimulationMetadata.ModelInfo, 'StopTime')
                current = double(out.SimulationMetadata.ModelInfo.StopTime);
            elseif strcmp(execution.StopEvent, 'ReachedStopTime')
                current = stopTime;
            end
            if ~isempty(out.ErrorMessage)
                state = 'failed';
                diagnostic = out.ErrorMessage;
            elseif strcmp(execution.StopEvent, 'ReachedStopTime')
                state = 'completed';
            else
                state = 'stopped';
            end
        end
        result = jsonencode(struct('state', state, 'simulation_time', current, ...
            'start_time', startTime, 'stop_time', stopTime, 'diagnostic', diagnostic));
    case 'stop'
        if ~isempty(sm) && ~strcmpi(string(sm.Status), 'inactive')
            out = stop(sm);
            assignin('base', 'portal_last_simulation_output', out);
        end
    case 'release'
        sm = [];
        if ~isempty(warningConfig)
            warning(warningConfig);
            warningConfig = [];
        end
        if ~isempty(cacheConfig)
            Simulink.fileGenControl('setConfig', 'config', cacheConfig);
            cacheConfig = [];
        end
    otherwise
        error('Portal:Action', 'Unknown simulation action.');
end
end
