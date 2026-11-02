function result = portal_export_waveforms(out, folder, scopes)
% Publish the exact scope samples. Browser requests reduce only the viewed range.
if ~isfolder(folder)
    mkdir(folder);
end
index = struct('id', {}, 'name', {}, 'channels', {}, 'start', {}, 'end', {});
available = who(out);
for k = 1:numel(scopes)
    variable = ['portal_scope_' scopes(k).id];
    if ~ismember(variable, available)
        error('Portal:ScopeData', 'Scope data is missing: %s', scopes(k).name);
    end
    dataset = out.get(variable);
    channels = struct('id', {}, 'label', {}, 'group', {}, 'count', {}, 'file', {});
    first = Inf;
    last = -Inf;
    for port = 1:dataset.numElements
        signal = dataset.getElement(port);
        entries = unpack(signal.Values, char(signal.Name));
        for j = 1:numel(entries)
            ts = entries(j).value;
            time = double(ts.Time(:));
            if isempty(time)
                continue;
            end
            data = double(ts.Data);
            if ~ts.IsTimeFirst
                data = permute(data, [ndims(data), 1:ndims(data)-1]);
            end
            data = reshape(data, numel(time), []);
            if ~isreal(data) || any(~isfinite(time)) || any(diff(time) < 0)
                error('Portal:ScopeData', 'Unsupported complex or unordered scope data.');
            end
            for column = 1:size(data, 2)
                number = numel(channels) + 1;
                label = entries(j).label;
                if isempty(label)
                    label = sprintf('通道 %d', port);
                end
                if size(data, 2) > 1
                    label = sprintf('%s [%d]', label, column);
                end
                filename = sprintf('%s_%d.bin', scopes(k).id, number);
                pending = fullfile(folder, [filename '.pending']);
                file = fopen(pending, 'w', 'ieee-le');
                if file < 0
                    error('Portal:ScopeFile', 'Cannot create waveform data.');
                end
                guard = onCleanup(@() fclose(file));
                written = fwrite(file, [time, data(:, column)]', 'double');
                clear guard;
                if written ~= 2*numel(time)
                    error('Portal:ScopeFile', 'Incomplete waveform data.');
                end
                movefile(pending, fullfile(folder, filename), 'f');
                channels(number) = struct('id', number, 'label', label, 'group', port, ...
                    'count', uint64(numel(time)), 'file', filename);
                first = min(first, time(1));
                last = max(last, time(end));
            end
        end
    end
    if isempty(channels)
        error('Portal:ScopeData', 'No scope samples were recorded.');
    end
    index(k) = struct('id', scopes(k).id, 'name', scopes(k).name, ...
        'channels', {channels}, 'start', first, 'end', last);
end
pending = fullfile(folder, 'index.json.pending');
file = fopen(pending, 'w', 'n', 'UTF-8');
if file < 0
    error('Portal:ScopeFile', 'Cannot publish waveform index.');
end
guard = onCleanup(@() fclose(file));
fwrite(file, unicode2native(jsonencode(index), 'UTF-8'), 'uint8');
clear guard;
movefile(pending, fullfile(folder, 'index.json'), 'f');
result = jsonencode(struct('ready', true));
end

function entries = unpack(value, label)
entries = struct('label', {}, 'value', {});
if isa(value, 'timeseries')
    entries(1) = struct('label', label, 'value', value);
elseif isa(value, 'Simulink.SimulationData.Dataset')
    for k = 1:value.numElements
        signal = value.getElement(k);
        entries = [entries, unpack(signal.Values, char(signal.Name))]; %#ok<AGROW>
    end
elseif isstruct(value)
    names = fieldnames(value);
    for k = 1:numel(names)
        entries = [entries, unpack(value.(names{k}), names{k})]; %#ok<AGROW>
    end
else
    error('Portal:ScopeData', 'Unsupported scope sample format.');
end
end
