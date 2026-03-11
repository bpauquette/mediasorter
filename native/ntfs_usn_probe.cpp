#include <windows.h>
#include <winioctl.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <io.h>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>
#include <fcntl.h>

namespace {

constexpr DWORD NTFS_ATTRIBUTE_TYPE_DATA = 0x80;
constexpr DWORD MINIMUM_VOLUME_READ_ACCESS = FILE_READ_ATTRIBUTES;

#pragma pack(push, 1)
struct FileRecordHeader {
    DWORD magic = 0;
    WORD usa_offset = 0;
    WORD usa_count = 0;
    ULONGLONG lsn = 0;
    WORD sequence_number = 0;
    WORD hard_link_count = 0;
    WORD first_attribute_offset = 0;
    WORD flags = 0;
    DWORD bytes_in_use = 0;
    DWORD bytes_allocated = 0;
    ULONGLONG base_file_record = 0;
    WORD next_attribute_id = 0;
    WORD alignment = 0;
    DWORD mft_record_number = 0;
};

struct AttributeRecordHeader {
    DWORD type = 0;
    DWORD length = 0;
    BYTE non_resident = 0;
    BYTE name_length = 0;
    WORD name_offset = 0;
    WORD flags = 0;
    WORD instance = 0;
};

struct ResidentAttributeBody {
    DWORD value_length = 0;
    WORD value_offset = 0;
    BYTE resident_flags = 0;
    BYTE reserved = 0;
};

struct NonResidentAttributeBody {
    ULONGLONG lowest_vcn = 0;
    ULONGLONG highest_vcn = 0;
    WORD mapping_pairs_offset = 0;
    BYTE compression_unit = 0;
    BYTE reserved[5]{};
    ULONGLONG allocated_size = 0;
    ULONGLONG data_size = 0;
    ULONGLONG initialized_size = 0;
    ULONGLONG compressed_size = 0;
};
#pragma pack(pop)

struct Win32ScanSummary {
    std::uint64_t files = 0;
    std::uint64_t directories = 0;
};

struct PrivilegeStatus {
    bool present = false;
    bool enabled = false;
    DWORD error = 0;
};

struct NtfsVolumeLayout {
    bool ok = false;
    DWORD error = 0;
    std::uint64_t bytes_per_sector = 0;
    std::uint64_t bytes_per_cluster = 0;
    std::uint64_t bytes_per_file_record = 0;
    std::uint64_t mft_start_lcn = 0;
    std::uint64_t mft2_start_lcn = 0;
    std::uint64_t clusters_per_file_record = 0;
};

struct VolumeOpenAttempt {
    const char* label = "";
    DWORD desired_access = 0;
    DWORD flags = 0;
    bool open_ok = false;
    DWORD open_error = 0;
    bool layout_ok = false;
    DWORD layout_error = 0;
    bool query_ok = false;
    DWORD query_error = 0;
    bool enum_ok = false;
    DWORD enum_error = 0;
    NtfsVolumeLayout layout{};
};

struct DataRunExtent {
    std::uint64_t lcn = 0;
    std::uint64_t clusters = 0;
};

struct NtfsSummaryEntry {
    std::uint64_t frn = 0;
    std::uint64_t parent_frn = 0;
    std::wstring name;
    bool is_dir = false;
    std::uint64_t size = 0;
};

struct CollectNtfsEntriesProfile {
    std::uint64_t ioctl_calls = 0;
    std::uint64_t records_seen = 0;
    std::uint64_t records_stored = 0;
    std::uint64_t duplicate_frns = 0;
    std::uint64_t bytes_returned = 0;
    std::uint64_t ioctl_ms = 0;
    std::uint64_t parse_ms = 0;
    std::uint64_t name_ms = 0;
    std::uint64_t store_ms = 0;
};

#pragma pack(push, 1)
struct BinaryNtfsHeader {
    char magic[8];
    std::uint32_t version = 1;
};

struct BinaryNtfsRecordHeader {
    std::uint64_t frn = 0;
    std::uint64_t parent_frn = 0;
    std::uint64_t size = 0;
    std::uint32_t name_bytes = 0;
    std::uint8_t is_dir = 0;
};

struct BinaryTreeHeader {
    char magic[8];
    std::uint32_t version = 1;
};

struct BinaryTreeNodeHeader {
    std::uint64_t size = 0;
    std::uint32_t child_count = 0;
    std::uint32_t name_bytes = 0;
    std::uint8_t is_dir = 0;
};
#pragma pack(pop)

std::wstring make_volume_path(const std::wstring& drive_root) {
    std::wstring path = drive_root;
    while (!path.empty() && (path.back() == L'\\' || path.back() == L'/')) {
        path.pop_back();
    }
    return LR"(\\.\)" + path;
}

std::wstring make_mft_file_path(const std::wstring& drive_root) {
    std::wstring path = drive_root;
    while (!path.empty() && (path.back() == L'\\' || path.back() == L'/')) {
        path.pop_back();
    }
    return path + LR"(\$MFT)";
}

std::string narrow_ascii(const std::wstring& value) {
    std::string out;
    out.reserve(value.size());
    for (wchar_t ch : value) {
        out.push_back(ch >= 0 && ch <= 0x7f ? static_cast<char>(ch) : '?');
    }
    return out;
}

std::string narrow_utf8(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int bytes_needed = WideCharToMultiByte(
        CP_UTF8,
        0,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr
    );
    if (bytes_needed <= 0) {
        return narrow_ascii(value);
    }
    std::string out(static_cast<size_t>(bytes_needed), '\0');
    WideCharToMultiByte(
        CP_UTF8,
        0,
        value.data(),
        static_cast<int>(value.size()),
        out.data(),
        bytes_needed,
        nullptr,
        nullptr
    );
    return out;
}

bool looks_like_binary_output_path(const std::wstring& out_path) {
    auto pos = out_path.find_last_of(L'.');
    if (pos == std::wstring::npos) {
        return false;
    }
    std::wstring ext = out_path.substr(pos);
    std::transform(ext.begin(), ext.end(), ext.begin(), [](wchar_t ch) { return static_cast<wchar_t>(towlower(ch)); });
    return ext == L".bin";
}

bool looks_like_tree_output_path(const std::wstring& out_path) {
    auto pos = out_path.find_last_of(L'.');
    if (pos == std::wstring::npos) {
        return false;
    }
    std::wstring ext = out_path.substr(pos);
    std::transform(ext.begin(), ext.end(), ext.begin(), [](wchar_t ch) { return static_cast<wchar_t>(towlower(ch)); });
    return ext == L".treebin";
}

bool write_binary_ntfs_header(std::ostream& out) {
    BinaryNtfsHeader header{{'M','S','N','T','F','S','0','1'}, 1};
    out.write(reinterpret_cast<const char*>(&header), sizeof(header));
    return static_cast<bool>(out);
}

bool write_binary_tree_header(std::ostream& out) {
    BinaryTreeHeader header{{'M','S','T','R','E','E','0','1'}, 1};
    out.write(reinterpret_cast<const char*>(&header), sizeof(header));
    return static_cast<bool>(out);
}

bool write_binary_ntfs_record(
    std::ostream& out,
    std::uint64_t frn,
    std::uint64_t parent_frn,
    bool is_dir,
    std::uint64_t size,
    const std::wstring& name
) {
    const std::string utf8_name = narrow_utf8(name);
    BinaryNtfsRecordHeader header{};
    header.frn = frn;
    header.parent_frn = parent_frn;
    header.size = size;
    header.name_bytes = static_cast<std::uint32_t>(utf8_name.size());
    header.is_dir = is_dir ? 1 : 0;
    out.write(reinterpret_cast<const char*>(&header), sizeof(header));
    if (!utf8_name.empty()) {
        out.write(utf8_name.data(), static_cast<std::streamsize>(utf8_name.size()));
    }
    return static_cast<bool>(out);
}

bool write_binary_tree_node(
    std::ostream& out,
    std::uint64_t size,
    std::uint32_t child_count,
    bool is_dir,
    const std::wstring& name
) {
    const std::string utf8_name = narrow_utf8(name);
    BinaryTreeNodeHeader header{};
    header.size = size;
    header.child_count = child_count;
    header.name_bytes = static_cast<std::uint32_t>(utf8_name.size());
    header.is_dir = is_dir ? 1 : 0;
    out.write(reinterpret_cast<const char*>(&header), sizeof(header));
    if (!utf8_name.empty()) {
        out.write(utf8_name.data(), static_cast<std::streamsize>(utf8_name.size()));
    }
    return static_cast<bool>(out);
}

std::string json_escape(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (char ch : value) {
        switch (ch) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out.push_back(ch); break;
        }
    }
    return out;
}

void print_json_bool(const char* key, bool value, bool trailing_comma = true) {
    std::cout << "  \"" << key << "\": " << (value ? "true" : "false");
    if (trailing_comma) {
        std::cout << ",";
    }
    std::cout << "\n";
}

void print_json_number(const char* key, std::uint64_t value, bool trailing_comma = true) {
    std::cout << "  \"" << key << "\": " << value;
    if (trailing_comma) {
        std::cout << ",";
    }
    std::cout << "\n";
}

void print_json_string(const char* key, const std::string& value, bool trailing_comma = true) {
    std::cout << "  \"" << key << "\": \"" << value << "\"";
    if (trailing_comma) {
        std::cout << ",";
    }
    std::cout << "\n";
}

PrivilegeStatus enable_privilege(const wchar_t* privilege_name) {
    PrivilegeStatus status{};
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) {
        status.error = GetLastError();
        return status;
    }

    LUID luid{};
    if (!LookupPrivilegeValueW(nullptr, privilege_name, &luid)) {
        status.error = GetLastError();
        CloseHandle(token);
        return status;
    }
    status.present = true;

    TOKEN_PRIVILEGES tp{};
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Luid = luid;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;

    SetLastError(ERROR_SUCCESS);
    if (!AdjustTokenPrivileges(token, FALSE, &tp, sizeof(tp), nullptr, nullptr)) {
        status.error = GetLastError();
        CloseHandle(token);
        return status;
    }

    DWORD last_error = GetLastError();
    if (last_error == ERROR_SUCCESS) {
        status.enabled = true;
    } else {
        status.error = last_error;
    }
    CloseHandle(token);
    return status;
}

NtfsVolumeLayout query_ntfs_volume_layout(HANDLE volume) {
    NtfsVolumeLayout layout{};
    NTFS_VOLUME_DATA_BUFFER data{};
    DWORD returned = 0;
    if (!DeviceIoControl(
            volume,
            FSCTL_GET_NTFS_VOLUME_DATA,
            nullptr,
            0,
            &data,
            sizeof(data),
            &returned,
            nullptr)) {
        layout.error = GetLastError();
        return layout;
    }

    layout.ok = true;
    layout.bytes_per_sector = static_cast<std::uint64_t>(data.BytesPerSector);
    layout.bytes_per_cluster = static_cast<std::uint64_t>(data.BytesPerCluster);
    layout.bytes_per_file_record = static_cast<std::uint64_t>(data.BytesPerFileRecordSegment);
    layout.mft_start_lcn = static_cast<std::uint64_t>(data.MftStartLcn.QuadPart);
    layout.mft2_start_lcn = static_cast<std::uint64_t>(data.Mft2StartLcn.QuadPart);
    layout.clusters_per_file_record =
        data.BytesPerCluster > 0
            ? static_cast<std::uint64_t>(data.BytesPerFileRecordSegment / data.BytesPerCluster)
            : 0;
    return layout;
}

VolumeOpenAttempt probe_volume_attempt(
    const std::wstring& volume_path,
    const char* label,
    DWORD desired_access,
    DWORD flags = FILE_ATTRIBUTE_NORMAL) {
    VolumeOpenAttempt attempt{};
    attempt.label = label;
    attempt.desired_access = desired_access;
    attempt.flags = flags;

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        desired_access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        flags,
        nullptr
    );

    attempt.open_ok = volume != INVALID_HANDLE_VALUE;
    if (!attempt.open_ok) {
        attempt.open_error = GetLastError();
        return attempt;
    }

    attempt.layout = query_ntfs_volume_layout(volume);
    attempt.layout_ok = attempt.layout.ok;
    attempt.layout_error = attempt.layout.error;

    DWORD returned = 0;
    USN_JOURNAL_DATA_V0 journal{};
    if (DeviceIoControl(
            volume,
            FSCTL_QUERY_USN_JOURNAL,
            nullptr,
            0,
            &journal,
            sizeof(journal),
            &returned,
            nullptr)) {
        attempt.query_ok = true;
    } else {
        attempt.query_error = GetLastError();
    }

    MFT_ENUM_DATA_V0 med{};
    med.StartFileReferenceNumber = 0;
    med.LowUsn = 0;
    med.HighUsn = MAXLONGLONG;

    BYTE buffer[65536];
    returned = 0;
    if (DeviceIoControl(
            volume,
            FSCTL_ENUM_USN_DATA,
            &med,
            sizeof(med),
            buffer,
            sizeof(buffer),
            &returned,
            nullptr)) {
        attempt.enum_ok = true;
    } else {
        attempt.enum_error = GetLastError();
    }

    CloseHandle(volume);
    return attempt;
}

std::optional<std::uint64_t> parse_file_size_from_record(
    const BYTE* record_bytes,
    DWORD record_length) {
    if (record_length < sizeof(FileRecordHeader)) {
        return std::nullopt;
    }

    const auto* header = reinterpret_cast<const FileRecordHeader*>(record_bytes);
    if (header->magic != 0x454c4946) {  // "FILE"
        return std::nullopt;
    }

    DWORD offset = header->first_attribute_offset;
    while (offset + sizeof(AttributeRecordHeader) <= record_length) {
        const auto* attr = reinterpret_cast<const AttributeRecordHeader*>(record_bytes + offset);
        if (attr->type == 0xFFFFFFFF) {
            break;
        }
        if (attr->length == 0 || offset + attr->length > record_length) {
            break;
        }

        if (attr->type == NTFS_ATTRIBUTE_TYPE_DATA && attr->name_length == 0) {
            if (attr->non_resident == 0) {
                if (attr->length < sizeof(AttributeRecordHeader) + sizeof(ResidentAttributeBody)) {
                    return std::nullopt;
                }
                const auto* body = reinterpret_cast<const ResidentAttributeBody*>(
                    record_bytes + offset + sizeof(AttributeRecordHeader)
                );
                return static_cast<std::uint64_t>(body->value_length);
            }

            if (attr->length < sizeof(AttributeRecordHeader) + sizeof(NonResidentAttributeBody)) {
                return std::nullopt;
            }
            const auto* body = reinterpret_cast<const NonResidentAttributeBody*>(
                record_bytes + offset + sizeof(AttributeRecordHeader)
            );
            return static_cast<std::uint64_t>(body->allocated_size);
        }

        offset += attr->length;
    }
    return std::nullopt;
}

bool query_ntfs_file_size(HANDLE volume, DWORDLONG frn, std::uint64_t& size_out, DWORD& error_out) {
    NTFS_FILE_RECORD_INPUT_BUFFER input{};
    input.FileReferenceNumber.QuadPart = static_cast<LONGLONG>(frn);

    std::vector<BYTE> buffer(64 * 1024);
    DWORD returned = 0;
    if (!DeviceIoControl(
            volume,
            FSCTL_GET_NTFS_FILE_RECORD,
            &input,
            sizeof(input),
            buffer.data(),
            static_cast<DWORD>(buffer.size()),
            &returned,
            nullptr)) {
        error_out = GetLastError();
        return false;
    }

    if (returned < sizeof(NTFS_FILE_RECORD_OUTPUT_BUFFER)) {
        error_out = ERROR_INVALID_DATA;
        return false;
    }

    const auto* output = reinterpret_cast<const NTFS_FILE_RECORD_OUTPUT_BUFFER*>(buffer.data());
    const BYTE* record_bytes = output->FileRecordBuffer;
    const DWORD record_length = output->FileRecordLength;
    auto parsed = parse_file_size_from_record(record_bytes, record_length);
    if (!parsed.has_value()) {
        error_out = ERROR_INVALID_DATA;
        return false;
    }

    size_out = parsed.value();
    error_out = 0;
    return true;
}

bool read_volume_bytes(HANDLE volume, std::uint64_t offset, BYTE* buffer, DWORD length, DWORD& error_out) {
    LARGE_INTEGER li{};
    li.QuadPart = static_cast<LONGLONG>(offset);
    if (!SetFilePointerEx(volume, li, nullptr, FILE_BEGIN)) {
        error_out = GetLastError();
        return false;
    }

    DWORD total = 0;
    while (total < length) {
        DWORD bytes_read = 0;
        if (!ReadFile(volume, buffer + total, length - total, &bytes_read, nullptr)) {
            error_out = GetLastError();
            return false;
        }
        if (bytes_read == 0) {
            error_out = ERROR_HANDLE_EOF;
            return false;
        }
        total += bytes_read;
    }

    error_out = 0;
    return true;
}

bool apply_fixup(BYTE* record_bytes, DWORD record_length, std::uint64_t bytes_per_sector) {
    if (record_length < sizeof(FileRecordHeader)) {
        return false;
    }
    const auto* header = reinterpret_cast<const FileRecordHeader*>(record_bytes);
    if (header->usa_offset + header->usa_count * sizeof(WORD) > record_length) {
        return false;
    }
    auto* usa = reinterpret_cast<WORD*>(record_bytes + header->usa_offset);
    const WORD usn = usa[0];
    for (WORD i = 1; i < header->usa_count; ++i) {
        const std::uint64_t sector_end = static_cast<std::uint64_t>(i) * bytes_per_sector - sizeof(WORD);
        if (sector_end + sizeof(WORD) > record_length) {
            return false;
        }
        auto* sector_word = reinterpret_cast<WORD*>(record_bytes + sector_end);
        if (*sector_word != usn) {
            return false;
        }
        *sector_word = usa[i];
    }
    return true;
}

std::vector<DataRunExtent> parse_data_runlist(const BYTE* record_bytes, DWORD record_length) {
    std::vector<DataRunExtent> extents;
    if (record_length < sizeof(FileRecordHeader)) {
        return extents;
    }

    const auto* header = reinterpret_cast<const FileRecordHeader*>(record_bytes);
    if (header->magic != 0x454c4946) {
        return extents;
    }

    DWORD offset = header->first_attribute_offset;
    while (offset + sizeof(AttributeRecordHeader) <= record_length) {
        const auto* attr = reinterpret_cast<const AttributeRecordHeader*>(record_bytes + offset);
        if (attr->type == 0xFFFFFFFF) {
            break;
        }
        if (attr->length == 0 || offset + attr->length > record_length) {
            break;
        }

        if (attr->type == NTFS_ATTRIBUTE_TYPE_DATA && attr->name_length == 0 && attr->non_resident != 0) {
            const auto* body = reinterpret_cast<const NonResidentAttributeBody*>(
                record_bytes + offset + sizeof(AttributeRecordHeader)
            );
            const BYTE* run = record_bytes + offset + body->mapping_pairs_offset;
            const BYTE* end = record_bytes + offset + attr->length;
            std::int64_t current_lcn = 0;
            while (run < end && *run != 0) {
                const BYTE header_byte = *run++;
                const BYTE len_size = header_byte & 0x0F;
                const BYTE off_size = (header_byte >> 4) & 0x0F;
                if (len_size == 0 || run + len_size + off_size > end) {
                    extents.clear();
                    return extents;
                }

                std::uint64_t cluster_len = 0;
                for (BYTE i = 0; i < len_size; ++i) {
                    cluster_len |= static_cast<std::uint64_t>(run[i]) << (8 * i);
                }
                run += len_size;

                std::int64_t lcn_delta = 0;
                if (off_size > 0) {
                    for (BYTE i = 0; i < off_size; ++i) {
                        lcn_delta |= static_cast<std::int64_t>(run[i]) << (8 * i);
                    }
                    if (run[off_size - 1] & 0x80) {
                        lcn_delta |= -((std::int64_t)1 << (off_size * 8));
                    }
                }
                run += off_size;

                current_lcn += lcn_delta;
                extents.push_back(DataRunExtent{
                    static_cast<std::uint64_t>(current_lcn),
                    cluster_len,
                });
            }
            break;
        }

        offset += attr->length;
    }

    return extents;
}

bool read_mft_record_zero(
    HANDLE volume,
    const NtfsVolumeLayout& layout,
    std::vector<BYTE>& record_out,
    DWORD& error_out) {
    record_out.assign(static_cast<size_t>(layout.bytes_per_file_record), 0);
    const std::uint64_t offset = layout.mft_start_lcn * layout.bytes_per_cluster;
    if (!read_volume_bytes(
            volume,
            offset,
            record_out.data(),
            static_cast<DWORD>(record_out.size()),
            error_out)) {
        return false;
    }
    if (!apply_fixup(record_out.data(), static_cast<DWORD>(record_out.size()), layout.bytes_per_sector)) {
        error_out = ERROR_INVALID_DATA;
        return false;
    }
    return true;
}

void write_stream_record(std::ofstream& out, bool is_dir, std::uint64_t size, const std::wstring& path) {
    out << (is_dir ? 'D' : 'F') << '\t' << size << '\t' << json_escape(narrow_ascii(path)) << '\n';
}

void scan_win32_recursive(const std::wstring& root_path, std::ofstream& out, Win32ScanSummary& summary) {
    std::wstring query = root_path;
    if (!query.empty() && query.back() != L'\\') {
        query += L'\\';
    }
    std::wstring pattern = query + L'*';

    WIN32_FIND_DATAW find_data{};
    HANDLE handle = FindFirstFileW(pattern.c_str(), &find_data);
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }

    do {
        const std::wstring name = find_data.cFileName;
        if (name == L"." || name == L"..") {
            continue;
        }

        std::wstring full_path = query + name;
        const bool is_dir = (find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        if (is_dir) {
            ++summary.directories;
            write_stream_record(out, true, 0, full_path);
            scan_win32_recursive(full_path, out, summary);
        } else {
            ++summary.files;
            const std::uint64_t size =
                (static_cast<std::uint64_t>(find_data.nFileSizeHigh) << 32) |
                static_cast<std::uint64_t>(find_data.nFileSizeLow);
            write_stream_record(out, false, size, full_path);
        }
    } while (FindNextFileW(handle, &find_data));

    FindClose(handle);
}

int run_probe(const std::wstring& drive_root) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    const PrivilegeStatus manage_volume{};
    const PrivilegeStatus backup{};
    const PrivilegeStatus restore{};

    const VolumeOpenAttempt attempts[] = {
        probe_volume_attempt(volume_path, "file_read_attributes", MINIMUM_VOLUME_READ_ACCESS),
        probe_volume_attempt(volume_path, "generic_read", GENERIC_READ),
        probe_volume_attempt(volume_path, "zero_access", 0),
    };

    const VolumeOpenAttempt* best = &attempts[0];
    for (const auto& attempt : attempts) {
        if (attempt.enum_ok) {
            best = &attempt;
            break;
        }
        if (attempt.query_ok || attempt.layout_ok) {
            best = &attempt;
            break;
        }
        if (!best->open_ok && attempt.open_ok) {
            best = &attempt;
        }
    }

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "probe");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_bool("se_manage_volume_present", manage_volume.present);
    print_json_bool("se_manage_volume_enabled", manage_volume.enabled);
    print_json_number("se_manage_volume_error", manage_volume.error);
    print_json_bool("se_backup_present", backup.present);
    print_json_bool("se_backup_enabled", backup.enabled);
    print_json_number("se_backup_error", backup.error);
    print_json_bool("se_restore_present", restore.present);
    print_json_bool("se_restore_enabled", restore.enabled);
    print_json_number("se_restore_error", restore.error);
    print_json_string("selected_attempt", best->label);
    print_json_number("selected_desired_access", best->desired_access);
    print_json_bool("open_ok", best->open_ok);
    print_json_number("open_error", best->open_error);
    print_json_bool("ntfs_layout_ok", best->layout_ok);
    print_json_number("ntfs_layout_error", best->layout_error);
    print_json_number("bytes_per_sector", best->layout.bytes_per_sector);
    print_json_number("bytes_per_cluster", best->layout.bytes_per_cluster);
    print_json_number("bytes_per_file_record", best->layout.bytes_per_file_record);
    print_json_number("mft_start_lcn", best->layout.mft_start_lcn);
    print_json_number("mft2_start_lcn", best->layout.mft2_start_lcn);
    print_json_number("clusters_per_file_record", best->layout.clusters_per_file_record);
    print_json_bool("query_ok", best->query_ok);
    print_json_number("query_error", best->query_error);
    print_json_bool("enum_ok", best->enum_ok);
    print_json_number("enum_error", best->enum_error);
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_scan_win32(const std::wstring& root_path, const std::wstring& out_path) {
    const auto started_at = std::chrono::steady_clock::now();
    std::ofstream out(narrow_ascii(out_path), std::ios::binary | std::ios::trunc);
    if (!out) {
        std::cerr << "failed to open output file" << std::endl;
        return 1;
    }

    Win32ScanSummary summary{};
    scan_win32_recursive(root_path, out, summary);
    out.flush();
    out.close();

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "scan-win32");
    print_json_string("drive", json_escape(narrow_ascii(root_path)));
    print_json_number("directories", summary.directories);
    print_json_number("files", summary.files);
    print_json_string("stream_path", json_escape(narrow_ascii(out_path)));
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_scan_usn(const std::wstring& drive_root, const std::wstring& out_path) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    const PrivilegeStatus manage_volume{};
    const PrivilegeStatus backup{};
    const PrivilegeStatus restore{};

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );

    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }

    USN_JOURNAL_DATA_V0 journal{};
    DWORD returned = 0;
    if (!DeviceIoControl(
            volume,
            FSCTL_QUERY_USN_JOURNAL,
            nullptr,
            0,
            &journal,
            sizeof(journal),
            &returned,
            nullptr)) {
        const DWORD error = GetLastError();
        CloseHandle(volume);
        std::cerr << "failed to query usn journal: " << error << std::endl;
        return 1;
    }

    std::ofstream out(narrow_ascii(out_path), std::ios::binary | std::ios::trunc);
    if (!out) {
        CloseHandle(volume);
        std::cerr << "failed to open output file" << std::endl;
        return 1;
    }

    std::uint64_t records = 0;
    std::uint64_t directories = 0;
    std::uint64_t files = 0;

    MFT_ENUM_DATA_V0 med{};
    med.StartFileReferenceNumber = 0;
    med.LowUsn = 0;
    med.HighUsn = journal.NextUsn;

    std::vector<BYTE> buffer(1024 * 1024);
    while (true) {
        returned = 0;
        if (!DeviceIoControl(
                volume,
                FSCTL_ENUM_USN_DATA,
                &med,
                sizeof(med),
                buffer.data(),
                static_cast<DWORD>(buffer.size()),
                &returned,
                nullptr)) {
            const DWORD error = GetLastError();
            if (error == ERROR_HANDLE_EOF) {
                break;
            }
            out.close();
            CloseHandle(volume);
            std::cerr << "failed to enumerate usn data: " << error << std::endl;
            return 1;
        }

        if (returned < sizeof(USN)) {
            break;
        }

        const USN* next_frn = reinterpret_cast<const USN*>(buffer.data());
        med.StartFileReferenceNumber = *next_frn;

        DWORD offset = sizeof(USN);
        while (offset + sizeof(USN_RECORD_V2) <= returned) {
            const auto* record = reinterpret_cast<const USN_RECORD_V2*>(buffer.data() + offset);
            if (record->RecordLength == 0 || offset + record->RecordLength > returned) {
                break;
            }

            const wchar_t* file_name = reinterpret_cast<const wchar_t*>(
                reinterpret_cast<const BYTE*>(record) + record->FileNameOffset
            );
            const std::wstring name(file_name, record->FileNameLength / sizeof(wchar_t));
            const bool is_dir = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;

            out << "R\t"
                << static_cast<std::uint64_t>(record->FileReferenceNumber)
                << '\t'
                << static_cast<std::uint64_t>(record->ParentFileReferenceNumber)
                << '\t'
                << (is_dir ? 1 : 0)
                << '\t'
                << json_escape(narrow_ascii(name))
                << '\n';

            ++records;
            if (is_dir) {
                ++directories;
            } else {
                ++files;
            }

            offset += record->RecordLength;
        }
    }

    out.flush();
    out.close();
    CloseHandle(volume);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "scan-usn");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_bool("se_manage_volume_enabled", manage_volume.enabled);
    print_json_bool("se_backup_enabled", backup.enabled);
    print_json_bool("se_restore_enabled", restore.enabled);
    print_json_number("records", records);
    print_json_number("directories", directories);
    print_json_number("files", files);
    print_json_string("stream_path", json_escape(narrow_ascii(out_path)));
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_bench_file_sizes(const std::wstring& drive_root, std::uint64_t limit) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    enable_privilege(L"SeManageVolumePrivilege");
    enable_privilege(L"SeBackupPrivilege");
    enable_privilege(L"SeRestorePrivilege");

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );

    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }

    USN_JOURNAL_DATA_V0 journal{};
    DWORD returned = 0;
    if (!DeviceIoControl(
            volume,
            FSCTL_QUERY_USN_JOURNAL,
            nullptr,
            0,
            &journal,
            sizeof(journal),
            &returned,
            nullptr)) {
        const DWORD error = GetLastError();
        CloseHandle(volume);
        std::cerr << "failed to query usn journal: " << error << std::endl;
        return 1;
    }

    std::uint64_t sampled_files = 0;
    std::uint64_t parsed_sizes = 0;
    std::uint64_t failed_sizes = 0;
    std::uint64_t total_size = 0;
    DWORD last_error = 0;

    MFT_ENUM_DATA_V0 med{};
    med.StartFileReferenceNumber = 0;
    med.LowUsn = 0;
    med.HighUsn = journal.NextUsn;

    std::vector<BYTE> buffer(1024 * 1024);
    while (sampled_files < limit) {
        returned = 0;
        if (!DeviceIoControl(
                volume,
                FSCTL_ENUM_USN_DATA,
                &med,
                sizeof(med),
                buffer.data(),
                static_cast<DWORD>(buffer.size()),
                &returned,
                nullptr)) {
            last_error = GetLastError();
            if (last_error == ERROR_HANDLE_EOF) {
                last_error = 0;
                break;
            }
            CloseHandle(volume);
            std::cerr << "failed to enumerate usn data: " << last_error << std::endl;
            return 1;
        }

        if (returned < sizeof(USN)) {
            break;
        }

        const USN* next_frn = reinterpret_cast<const USN*>(buffer.data());
        med.StartFileReferenceNumber = *next_frn;

        DWORD offset = sizeof(USN);
        while (offset + sizeof(USN_RECORD_V2) <= returned && sampled_files < limit) {
            const auto* record = reinterpret_cast<const USN_RECORD_V2*>(buffer.data() + offset);
            if (record->RecordLength == 0 || offset + record->RecordLength > returned) {
                break;
            }

            const bool is_dir = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
            if (!is_dir) {
                ++sampled_files;
                std::uint64_t size = 0;
                DWORD query_error = 0;
                if (query_ntfs_file_size(volume, static_cast<DWORDLONG>(record->FileReferenceNumber), size, query_error)) {
                    ++parsed_sizes;
                    total_size += size;
                } else {
                    ++failed_sizes;
                    last_error = query_error;
                }
            }

            offset += record->RecordLength;
        }
    }

    CloseHandle(volume);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "bench-file-sizes");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_number("sample_limit", limit);
    print_json_number("sampled_files", sampled_files);
    print_json_number("parsed_sizes", parsed_sizes);
    print_json_number("failed_sizes", failed_sizes);
    print_json_number("total_sample_size", total_size);
    print_json_number("last_error", last_error);
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_probe_mft_file(const std::wstring& drive_root) {
    const std::wstring mft_path = make_mft_file_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    PrivilegeStatus backup = enable_privilege(L"SeBackupPrivilege");
    PrivilegeStatus restore = enable_privilege(L"SeRestorePrivilege");

    HANDLE handle = CreateFileW(
        mft_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_SEQUENTIAL_SCAN,
        nullptr
    );

    DWORD open_error = 0;
    LARGE_INTEGER file_size{};
    bool size_ok = false;
    DWORD size_error = 0;
    if (handle == INVALID_HANDLE_VALUE) {
        open_error = GetLastError();
    } else {
        if (GetFileSizeEx(handle, &file_size)) {
            size_ok = true;
        } else {
            size_error = GetLastError();
        }
        CloseHandle(handle);
    }

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "probe-mft-file");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_bool("se_backup_enabled", backup.enabled);
    print_json_bool("se_restore_enabled", restore.enabled);
    print_json_bool("open_ok", handle != INVALID_HANDLE_VALUE);
    print_json_number("open_error", open_error);
    print_json_bool("size_ok", size_ok);
    print_json_number("size_error", size_error);
    print_json_number("mft_file_size", static_cast<std::uint64_t>(file_size.QuadPart));
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_bench_raw_mft_sizes(const std::wstring& drive_root, std::uint64_t limit) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    enable_privilege(L"SeManageVolumePrivilege");
    enable_privilege(L"SeBackupPrivilege");
    enable_privilege(L"SeRestorePrivilege");

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );

    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }

    NtfsVolumeLayout layout = query_ntfs_volume_layout(volume);
    if (!layout.ok) {
        const DWORD error = layout.error;
        CloseHandle(volume);
        std::cerr << "failed to query ntfs layout: " << error << std::endl;
        return 1;
    }

    std::vector<BYTE> record_zero;
    DWORD error_out = 0;
    if (!read_mft_record_zero(volume, layout, record_zero, error_out)) {
        CloseHandle(volume);
        std::cerr << "failed to read mft record zero: " << error_out << std::endl;
        return 1;
    }

    const auto extents = parse_data_runlist(record_zero.data(), static_cast<DWORD>(record_zero.size()));
    if (extents.empty()) {
        CloseHandle(volume);
        std::cerr << "failed to parse mft runlist" << std::endl;
        return 1;
    }

    std::vector<BYTE> chunk(8 * 1024 * 1024);
    const std::uint64_t record_size = layout.bytes_per_file_record;
    std::uint64_t sampled_records = 0;
    std::uint64_t parsed_sizes = 0;
    std::uint64_t directories = 0;
    std::uint64_t total_size = 0;
    DWORD last_error = 0;

    for (const auto& extent : extents) {
        if (sampled_records >= limit) {
            break;
        }
        const std::uint64_t extent_bytes = extent.clusters * layout.bytes_per_cluster;
        const std::uint64_t extent_offset = extent.lcn * layout.bytes_per_cluster;
        std::uint64_t consumed = 0;

        while (consumed + record_size <= extent_bytes && sampled_records < limit) {
            const std::uint64_t to_read = std::min<std::uint64_t>(chunk.size(), extent_bytes - consumed);
            const std::uint64_t aligned_read = to_read - (to_read % record_size);
            if (aligned_read < record_size) {
                break;
            }

            if (!read_volume_bytes(
                    volume,
                    extent_offset + consumed,
                    chunk.data(),
                    static_cast<DWORD>(aligned_read),
                    last_error)) {
                CloseHandle(volume);
                std::cerr << "failed raw mft read: " << last_error << std::endl;
                return 1;
            }

            const std::uint64_t record_count = aligned_read / record_size;
            for (std::uint64_t i = 0; i < record_count && sampled_records < limit; ++i) {
                BYTE* record_bytes = chunk.data() + (i * record_size);
                ++sampled_records;

                if (!apply_fixup(record_bytes, static_cast<DWORD>(record_size), layout.bytes_per_sector)) {
                    continue;
                }

                const auto* header = reinterpret_cast<const FileRecordHeader*>(record_bytes);
                if (header->magic != 0x454c4946 || (header->flags & 0x0001) == 0) {
                    continue;
                }

                if ((header->flags & 0x0002) != 0) {
                    ++directories;
                    continue;
                }

                auto parsed = parse_file_size_from_record(record_bytes, static_cast<DWORD>(record_size));
                if (parsed.has_value()) {
                    ++parsed_sizes;
                    total_size += parsed.value();
                }
            }

            consumed += aligned_read;
        }
    }

    CloseHandle(volume);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "bench-raw-mft-sizes");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_number("sample_limit", limit);
    print_json_number("sampled_records", sampled_records);
    print_json_number("parsed_sizes", parsed_sizes);
    print_json_number("directories", directories);
    print_json_number("mft_extents", extents.size());
    print_json_number("total_sample_size", total_size);
    print_json_number("last_error", last_error);
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

std::uint64_t file_record_index_from_frn(std::uint64_t frn) {
    return frn & 0x0000FFFFFFFFFFFFULL;
}

bool collect_raw_mft_sizes(
    HANDLE volume,
    const NtfsVolumeLayout& layout,
    std::vector<std::uint64_t>& sizes_out,
    DWORD& error_out) {
    std::vector<BYTE> record_zero;
    if (!read_mft_record_zero(volume, layout, record_zero, error_out)) {
        return false;
    }

    const auto extents = parse_data_runlist(record_zero.data(), static_cast<DWORD>(record_zero.size()));
    if (extents.empty()) {
        error_out = ERROR_INVALID_DATA;
        return false;
    }

    const std::uint64_t record_size = layout.bytes_per_file_record;
    std::vector<BYTE> chunk(8 * 1024 * 1024);
    std::uint64_t record_index = 0;

    for (const auto& extent : extents) {
        const std::uint64_t extent_bytes = extent.clusters * layout.bytes_per_cluster;
        const std::uint64_t extent_offset = extent.lcn * layout.bytes_per_cluster;
        std::uint64_t consumed = 0;

        while (consumed + record_size <= extent_bytes) {
            const std::uint64_t to_read = std::min<std::uint64_t>(chunk.size(), extent_bytes - consumed);
            const std::uint64_t aligned_read = to_read - (to_read % record_size);
            if (aligned_read < record_size) {
                break;
            }

            if (!read_volume_bytes(
                    volume,
                    extent_offset + consumed,
                    chunk.data(),
                    static_cast<DWORD>(aligned_read),
                    error_out)) {
                return false;
            }

            const std::uint64_t record_count = aligned_read / record_size;
            if (sizes_out.size() < record_index + record_count) {
                sizes_out.resize(static_cast<size_t>(record_index + record_count), 0);
            }

            for (std::uint64_t i = 0; i < record_count; ++i, ++record_index) {
                BYTE* record_bytes = chunk.data() + (i * record_size);
                if (!apply_fixup(record_bytes, static_cast<DWORD>(record_size), layout.bytes_per_sector)) {
                    continue;
                }

                const auto* header = reinterpret_cast<const FileRecordHeader*>(record_bytes);
                if (header->magic != 0x454c4946 || (header->flags & 0x0001) == 0 || (header->flags & 0x0002) != 0) {
                    continue;
                }

                auto parsed = parse_file_size_from_record(record_bytes, static_cast<DWORD>(record_size));
                if (parsed.has_value()) {
                    sizes_out[static_cast<size_t>(record_index)] = parsed.value();
                }
            }

            consumed += aligned_read;
        }
    }

    error_out = 0;
    return true;
}

bool collect_ntfs_entries(
    HANDLE volume,
    const std::vector<std::uint64_t>& size_by_record,
    std::unordered_map<std::uint64_t, NtfsSummaryEntry>& entries_out,
    std::vector<std::uint64_t>& order_out,
    DWORD& error_out,
    CollectNtfsEntriesProfile* profile_out = nullptr) {
    USN_JOURNAL_DATA_V0 journal{};
    DWORD returned = 0;
    if (!DeviceIoControl(
            volume,
            FSCTL_QUERY_USN_JOURNAL,
            nullptr,
            0,
            &journal,
            sizeof(journal),
            &returned,
            nullptr)) {
        error_out = GetLastError();
        return false;
    }

    MFT_ENUM_DATA_V0 med{};
    med.StartFileReferenceNumber = 0;
    med.LowUsn = 0;
    med.HighUsn = journal.NextUsn;

    if (entries_out.empty() && !size_by_record.empty()) {
        entries_out.reserve(size_by_record.size());
    }
    if (order_out.empty() && !size_by_record.empty()) {
        order_out.reserve(size_by_record.size());
    }

    auto phase_elapsed_ms = [](const std::chrono::steady_clock::time_point& phase_start) -> std::uint64_t {
        return static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - phase_start).count()
        );
    };

    CollectNtfsEntriesProfile local_profile{};
    CollectNtfsEntriesProfile& profile = profile_out ? *profile_out : local_profile;

    std::vector<BYTE> buffer(4 * 1024 * 1024);
    while (true) {
        returned = 0;
        auto ioctl_started_at = std::chrono::steady_clock::now();
        if (!DeviceIoControl(
                volume,
                FSCTL_ENUM_USN_DATA,
                &med,
                sizeof(med),
                buffer.data(),
                static_cast<DWORD>(buffer.size()),
                &returned,
                nullptr)) {
            profile.ioctl_ms += phase_elapsed_ms(ioctl_started_at);
            error_out = GetLastError();
            if (error_out == ERROR_HANDLE_EOF) {
                error_out = 0;
                break;
            }
            return false;
        }
        profile.ioctl_ms += phase_elapsed_ms(ioctl_started_at);
        ++profile.ioctl_calls;
        profile.bytes_returned += returned;

        if (returned < sizeof(USN)) {
            break;
        }

        const USN* next_frn = reinterpret_cast<const USN*>(buffer.data());
        med.StartFileReferenceNumber = *next_frn;

        DWORD offset = sizeof(USN);
        while (offset + sizeof(USN_RECORD_V2) <= returned) {
            auto parse_started_at = std::chrono::steady_clock::now();
            const auto* record = reinterpret_cast<const USN_RECORD_V2*>(buffer.data() + offset);
            if (record->RecordLength == 0 || offset + record->RecordLength > returned) {
                profile.parse_ms += phase_elapsed_ms(parse_started_at);
                break;
            }

            const std::uint64_t frn = static_cast<std::uint64_t>(record->FileReferenceNumber);
            const std::uint64_t parent_frn = static_cast<std::uint64_t>(record->ParentFileReferenceNumber);
            const bool is_dir = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
            const std::uint64_t record_index = file_record_index_from_frn(frn);
            const std::uint64_t size =
                (!is_dir && record_index < size_by_record.size()) ? size_by_record[static_cast<size_t>(record_index)] : 0;
            profile.parse_ms += phase_elapsed_ms(parse_started_at);

            auto name_started_at = std::chrono::steady_clock::now();
            std::wstring name(
                reinterpret_cast<const wchar_t*>(reinterpret_cast<const BYTE*>(record) + record->FileNameOffset),
                record->FileNameLength / sizeof(wchar_t)
            );
            profile.name_ms += phase_elapsed_ms(name_started_at);

            auto store_started_at = std::chrono::steady_clock::now();
            auto entry_it = entries_out.find(frn);
            if (entry_it == entries_out.end()) {
                entries_out.emplace(frn, NtfsSummaryEntry{
                    frn,
                    parent_frn,
                    std::move(name),
                    is_dir,
                    size,
                });
                order_out.push_back(frn);
                ++profile.records_stored;
            } else {
                entry_it->second.parent_frn = parent_frn;
                entry_it->second.name = std::move(name);
                entry_it->second.is_dir = is_dir;
                entry_it->second.size = size;
                ++profile.duplicate_frns;
            }
            profile.store_ms += phase_elapsed_ms(store_started_at);
            ++profile.records_seen;
            offset += record->RecordLength;
        }
    }

    return true;
}

int run_scan_ntfs(const std::wstring& drive_root, const std::wstring& out_path) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();
    auto phase_started_at = started_at;
    auto phase_elapsed_ms = [&](const std::chrono::steady_clock::time_point& phase_start) -> std::uint64_t {
        return static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - phase_start).count()
        );
    };
    std::uint64_t open_volume_ms = 0;
    std::uint64_t query_layout_ms = 0;
    std::uint64_t collect_mft_sizes_ms = 0;
    std::uint64_t collect_entries_ms = 0;
    std::uint64_t count_records_ms = 0;
    std::uint64_t rollup_sizes_ms = 0;
    std::uint64_t build_children_ms = 0;
    std::uint64_t sort_children_ms = 0;
    std::uint64_t write_output_ms = 0;
    CollectNtfsEntriesProfile collect_entries_profile{};

    const PrivilegeStatus manage_volume{};
    const PrivilegeStatus backup{};
    const PrivilegeStatus restore{};

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );

    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }
    open_volume_ms = phase_elapsed_ms(phase_started_at);

    phase_started_at = std::chrono::steady_clock::now();
    NtfsVolumeLayout layout = query_ntfs_volume_layout(volume);
    if (!layout.ok) {
        const DWORD error = layout.error;
        CloseHandle(volume);
        std::cerr << "failed to query ntfs layout: " << error << std::endl;
        return 1;
    }
    query_layout_ms = phase_elapsed_ms(phase_started_at);

    DWORD error_out = 0;
    std::vector<std::uint64_t> size_by_record;
    phase_started_at = std::chrono::steady_clock::now();
    if (!collect_raw_mft_sizes(volume, layout, size_by_record, error_out)) {
        CloseHandle(volume);
        std::cerr << "failed to collect raw mft sizes: " << error_out << std::endl;
        return 1;
    }
    collect_mft_sizes_ms = phase_elapsed_ms(phase_started_at);

    const bool stream_output = out_path == L"-";
    const bool tree_output = !stream_output && looks_like_tree_output_path(out_path);
    const bool binary_output = stream_output || (!tree_output && looks_like_binary_output_path(out_path));

    std::uint64_t records = 0;
    std::uint64_t directories = 0;
    std::uint64_t files = 0;
    std::uint64_t total_file_size = 0;

    std::unordered_map<std::uint64_t, NtfsSummaryEntry> entries;
    std::vector<std::uint64_t> order;
    if (stream_output || tree_output) {
        entries.reserve(4'500'000);
        order.reserve(4'500'000);
    }

    if (stream_output) {
        _setmode(_fileno(stdout), _O_BINARY);
    }

    if (stream_output) {
        BinaryNtfsHeader header{{'M','S','N','T','F','S','0','1'}, 1};
        std::cout.write(reinterpret_cast<const char*>(&header), sizeof(header));
        if (!std::cout.good()) {
            CloseHandle(volume);
            std::cerr << "failed to write stdout binary header" << std::endl;
            return 1;
        }
    }

    if (!stream_output) {
        phase_started_at = std::chrono::steady_clock::now();
        if (!collect_ntfs_entries(volume, size_by_record, entries, order, error_out, &collect_entries_profile)) {
            CloseHandle(volume);
            std::cerr << "failed to collect ntfs entries: " << error_out << std::endl;
            return 1;
        }
        CloseHandle(volume);
        collect_entries_ms = phase_elapsed_ms(phase_started_at);
    } else {
        USN_JOURNAL_DATA_V0 journal{};
        DWORD returned = 0;
        phase_started_at = std::chrono::steady_clock::now();
        if (!DeviceIoControl(
                volume,
                FSCTL_QUERY_USN_JOURNAL,
                nullptr,
                0,
                &journal,
                sizeof(journal),
                &returned,
                nullptr)) {
            const DWORD error = GetLastError();
            CloseHandle(volume);
            std::cerr << "failed to query usn journal: " << error << std::endl;
            return 1;
        }
        MFT_ENUM_DATA_V0 med{};
        med.StartFileReferenceNumber = 0;
        med.LowUsn = 0;
        med.HighUsn = journal.NextUsn;
        std::vector<BYTE> buffer(1024 * 1024);
        while (true) {
            returned = 0;
            if (!DeviceIoControl(
                    volume,
                    FSCTL_ENUM_USN_DATA,
                    &med,
                    sizeof(med),
                    buffer.data(),
                    static_cast<DWORD>(buffer.size()),
                    &returned,
                    nullptr)) {
                const DWORD error = GetLastError();
                if (error == ERROR_HANDLE_EOF) {
                    break;
                }
                CloseHandle(volume);
                std::cerr << "failed to enumerate usn data: " << error << std::endl;
                return 1;
            }
            if (returned < sizeof(USN)) {
                break;
            }
            const USN* next_frn = reinterpret_cast<const USN*>(buffer.data());
            med.StartFileReferenceNumber = *next_frn;
            DWORD offset = sizeof(USN);
            while (offset + sizeof(USN_RECORD_V2) <= returned) {
                const auto* record = reinterpret_cast<const USN_RECORD_V2*>(buffer.data() + offset);
                if (record->RecordLength == 0 || offset + record->RecordLength > returned) {
                    break;
                }
                const std::wstring name(
                    reinterpret_cast<const wchar_t*>(reinterpret_cast<const BYTE*>(record) + record->FileNameOffset),
                    record->FileNameLength / sizeof(wchar_t)
                );
                const bool is_dir = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                const std::uint64_t frn = static_cast<std::uint64_t>(record->FileReferenceNumber);
                const std::uint64_t parent_frn = static_cast<std::uint64_t>(record->ParentFileReferenceNumber);
                const std::uint64_t record_index = file_record_index_from_frn(frn);
                const std::uint64_t size =
                    (!is_dir && record_index < size_by_record.size()) ? size_by_record[static_cast<size_t>(record_index)] : 0;
                if (!write_binary_ntfs_record(std::cout, frn, parent_frn, is_dir, size, name)) {
                    CloseHandle(volume);
                    std::cerr << "failed to stream binary ntfs record" << std::endl;
                    return 1;
                }
                ++records;
                if (is_dir) {
                    ++directories;
                } else {
                    ++files;
                    total_file_size += size;
                }
                offset += record->RecordLength;
            }
        }
        std::cout.flush();
        CloseHandle(volume);
        collect_entries_ms = phase_elapsed_ms(phase_started_at);
    }

    phase_started_at = std::chrono::steady_clock::now();
    for (const auto& frn : order) {
        auto it = entries.find(frn);
        if (it == entries.end()) {
            continue;
        }
        if (it->second.is_dir) {
            ++directories;
        } else {
            ++files;
            total_file_size += it->second.size;
        }
        ++records;
    }
    count_records_ms = phase_elapsed_ms(phase_started_at);

    if (stream_output) {
        const auto finished_at = std::chrono::steady_clock::now();
        const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();
        std::cerr << "{\n";
        std::cerr << "  \"mode\": \"scan-ntfs-stream\",\n";
        std::cerr << "  \"drive\": \"" << json_escape(narrow_ascii(drive_root)) << "\",\n";
        std::cerr << "  \"records\": " << records << ",\n";
        std::cerr << "  \"directories\": " << directories << ",\n";
        std::cerr << "  \"files\": " << files << ",\n";
        std::cerr << "  \"total_file_size\": " << total_file_size << ",\n";
        std::cerr << "  \"collect_mft_sizes_ms\": " << collect_mft_sizes_ms << ",\n";
        std::cerr << "  \"collect_entries_ms\": " << collect_entries_ms << ",\n";
        std::cerr << "  \"elapsed_ms\": " << static_cast<std::uint64_t>(elapsed_ms) << "\n";
        std::cerr << "}\n";
        return 0;
    }

    phase_started_at = std::chrono::steady_clock::now();
    for (auto it = order.rbegin(); it != order.rend(); ++it) {
        auto child_it = entries.find(*it);
        if (child_it == entries.end()) {
            continue;
        }
        auto parent_it = entries.find(child_it->second.parent_frn);
        if (parent_it != entries.end()) {
            parent_it->second.size += child_it->second.size;
        }
    }
    rollup_sizes_ms = phase_elapsed_ms(phase_started_at);

    std::unordered_map<std::uint64_t, std::vector<std::uint64_t>> children_by_parent;
    children_by_parent.reserve(entries.size());
    std::vector<std::uint64_t> root_children;
    root_children.reserve(128);
    phase_started_at = std::chrono::steady_clock::now();
    for (const auto& frn : order) {
        auto it = entries.find(frn);
        if (it == entries.end()) {
            continue;
        }
        if (entries.find(it->second.parent_frn) != entries.end()) {
            children_by_parent[it->second.parent_frn].push_back(frn);
        } else {
            root_children.push_back(frn);
        }
    }
    build_children_ms = phase_elapsed_ms(phase_started_at);

    auto sort_by_size = [&](std::vector<std::uint64_t>& values) {
        std::sort(values.begin(), values.end(), [&](std::uint64_t left, std::uint64_t right) {
            return entries[left].size > entries[right].size;
        });
    };
    phase_started_at = std::chrono::steady_clock::now();
    sort_by_size(root_children);
    for (auto& pair : children_by_parent) {
        sort_by_size(pair.second);
    }
    sort_children_ms = phase_elapsed_ms(phase_started_at);

    phase_started_at = std::chrono::steady_clock::now();
    std::ofstream out;
    out.open(narrow_ascii(out_path), std::ios::binary | std::ios::trunc);
    if (!out) {
        std::cerr << "failed to open output file" << std::endl;
        return 1;
    }

    if (tree_output) {
        if (!write_binary_tree_header(out)) {
            std::cerr << "failed to write tree header" << std::endl;
            return 1;
        }
        if (!write_binary_tree_node(out, 0, static_cast<std::uint32_t>(root_children.size()), true, L"")) {
            std::cerr << "failed to write tree root header" << std::endl;
            return 1;
        }
        std::vector<std::uint64_t> stack;
        stack.reserve(order.size());
        for (auto it = root_children.rbegin(); it != root_children.rend(); ++it) {
            stack.push_back(*it);
        }
        while (!stack.empty()) {
            const std::uint64_t frn = stack.back();
            stack.pop_back();
            const auto entry_it = entries.find(frn);
            if (entry_it == entries.end()) {
                continue;
            }
            const auto children_it = children_by_parent.find(frn);
            const std::uint32_t child_count =
                children_it == children_by_parent.end() ? 0U : static_cast<std::uint32_t>(children_it->second.size());
            if (!write_binary_tree_node(out, entry_it->second.size, child_count, entry_it->second.is_dir, entry_it->second.name)) {
                std::cerr << "failed to write tree node" << std::endl;
                return 1;
            }
            if (children_it != children_by_parent.end()) {
                for (auto child_it = children_it->second.rbegin(); child_it != children_it->second.rend(); ++child_it) {
                    stack.push_back(*child_it);
                }
            }
        }
    } else {
        if (binary_output && !write_binary_ntfs_header(out)) {
            std::cerr << "failed to write binary header" << std::endl;
            return 1;
        }
        for (const auto& frn : order) {
            const auto entry_it = entries.find(frn);
            if (entry_it == entries.end()) {
                continue;
            }
            const auto& entry = entry_it->second;
            if (binary_output) {
                if (!write_binary_ntfs_record(out, entry.frn, entry.parent_frn, entry.is_dir, entry.size, entry.name)) {
                    std::cerr << "failed to write binary ntfs record" << std::endl;
                    return 1;
                }
            } else {
                out << "R\t"
                    << entry.frn << '\t'
                    << entry.parent_frn << '\t'
                    << (entry.is_dir ? 1 : 0) << '\t'
                    << entry.size << '\t'
                    << json_escape(narrow_ascii(entry.name))
                    << '\n';
            }
        }
    }

    out.flush();
    out.close();
    write_output_ms = phase_elapsed_ms(phase_started_at);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "scan-ntfs");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_bool("se_manage_volume_enabled", manage_volume.enabled);
    print_json_bool("se_backup_enabled", backup.enabled);
    print_json_bool("se_restore_enabled", restore.enabled);
    print_json_number("records", records);
    print_json_number("directories", directories);
    print_json_number("files", files);
    print_json_number("sized_records", size_by_record.size());
    print_json_number("total_file_size", total_file_size);
    print_json_string("stream_path", json_escape(narrow_ascii(out_path)));
    print_json_bool("tree_output", tree_output);
    print_json_bool("binary_output", binary_output);
    print_json_number("open_volume_ms", open_volume_ms);
    print_json_number("query_layout_ms", query_layout_ms);
    print_json_number("collect_mft_sizes_ms", collect_mft_sizes_ms);
    print_json_number("collect_entries_ms", collect_entries_ms);
    print_json_number("collect_entries_ioctl_calls", collect_entries_profile.ioctl_calls);
    print_json_number("collect_entries_records_seen", collect_entries_profile.records_seen);
    print_json_number("collect_entries_records_stored", collect_entries_profile.records_stored);
    print_json_number("collect_entries_duplicate_frns", collect_entries_profile.duplicate_frns);
    print_json_number("collect_entries_bytes_returned", collect_entries_profile.bytes_returned);
    print_json_number("collect_entries_ioctl_ms", collect_entries_profile.ioctl_ms);
    print_json_number("collect_entries_parse_ms", collect_entries_profile.parse_ms);
    print_json_number("collect_entries_name_ms", collect_entries_profile.name_ms);
    print_json_number("collect_entries_store_ms", collect_entries_profile.store_ms);
    print_json_number("count_records_ms", count_records_ms);
    print_json_number("rollup_sizes_ms", rollup_sizes_ms);
    print_json_number("build_children_ms", build_children_ms);
    print_json_number("sort_children_ms", sort_children_ms);
    print_json_number("write_output_ms", write_output_ms);
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_scan_ntfs_tree_stream(const std::wstring& drive_root) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();
    auto phase_started_at = started_at;
    auto phase_elapsed_ms = [&](const std::chrono::steady_clock::time_point& phase_start) -> std::uint64_t {
        return static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - phase_start).count()
        );
    };
    std::uint64_t collect_mft_sizes_ms = 0;
    std::uint64_t collect_entries_ms = 0;
    std::uint64_t rollup_sizes_ms = 0;
    std::uint64_t build_children_ms = 0;
    std::uint64_t sort_children_ms = 0;
    std::uint64_t write_output_ms = 0;
    CollectNtfsEntriesProfile collect_entries_profile{};

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );
    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }

    NtfsVolumeLayout layout = query_ntfs_volume_layout(volume);
    if (!layout.ok) {
        const DWORD error = layout.error;
        CloseHandle(volume);
        std::cerr << "failed to query ntfs layout: " << error << std::endl;
        return 1;
    }

    DWORD error_out = 0;
    std::vector<std::uint64_t> size_by_record;
    phase_started_at = std::chrono::steady_clock::now();
    if (!collect_raw_mft_sizes(volume, layout, size_by_record, error_out)) {
        CloseHandle(volume);
        std::cerr << "failed to collect raw mft sizes: " << error_out << std::endl;
        return 1;
    }
    collect_mft_sizes_ms = phase_elapsed_ms(phase_started_at);

    std::unordered_map<std::uint64_t, NtfsSummaryEntry> entries;
    std::vector<std::uint64_t> order;
    entries.reserve(4'500'000);
    order.reserve(4'500'000);
    phase_started_at = std::chrono::steady_clock::now();
    if (!collect_ntfs_entries(volume, size_by_record, entries, order, error_out, &collect_entries_profile)) {
        CloseHandle(volume);
        std::cerr << "failed to collect ntfs entries: " << error_out << std::endl;
        return 1;
    }
    CloseHandle(volume);
    collect_entries_ms = phase_elapsed_ms(phase_started_at);

    phase_started_at = std::chrono::steady_clock::now();
    for (auto it = order.rbegin(); it != order.rend(); ++it) {
        auto child_it = entries.find(*it);
        if (child_it == entries.end()) {
            continue;
        }
        auto parent_it = entries.find(child_it->second.parent_frn);
        if (parent_it != entries.end()) {
            parent_it->second.size += child_it->second.size;
        }
    }
    rollup_sizes_ms = phase_elapsed_ms(phase_started_at);

    std::unordered_map<std::uint64_t, std::vector<std::uint64_t>> children_by_parent;
    children_by_parent.reserve(entries.size());
    std::vector<std::uint64_t> root_children;
    root_children.reserve(128);
    phase_started_at = std::chrono::steady_clock::now();
    for (const auto& frn : order) {
        auto it = entries.find(frn);
        if (it == entries.end()) {
            continue;
        }
        if (entries.find(it->second.parent_frn) != entries.end()) {
            children_by_parent[it->second.parent_frn].push_back(frn);
        } else {
            root_children.push_back(frn);
        }
    }
    build_children_ms = phase_elapsed_ms(phase_started_at);

    auto sort_by_size = [&](std::vector<std::uint64_t>& values) {
        std::sort(values.begin(), values.end(), [&](std::uint64_t left, std::uint64_t right) {
            return entries[left].size > entries[right].size;
        });
    };
    phase_started_at = std::chrono::steady_clock::now();
    sort_by_size(root_children);
    for (auto& pair : children_by_parent) {
        sort_by_size(pair.second);
    }
    sort_children_ms = phase_elapsed_ms(phase_started_at);

    _setmode(_fileno(stdout), _O_BINARY);
    phase_started_at = std::chrono::steady_clock::now();
    if (!write_binary_tree_header(std::cout)) {
        std::cerr << "failed to write tree stream header" << std::endl;
        return 1;
    }
    if (!write_binary_tree_node(std::cout, 0, static_cast<std::uint32_t>(root_children.size()), true, L"")) {
        std::cerr << "failed to write tree stream root header" << std::endl;
        return 1;
    }
    std::vector<std::uint64_t> stack;
    stack.reserve(order.size());
    for (auto it = root_children.rbegin(); it != root_children.rend(); ++it) {
        stack.push_back(*it);
    }
    while (!stack.empty()) {
        const std::uint64_t frn = stack.back();
        stack.pop_back();
        const auto entry_it = entries.find(frn);
        if (entry_it == entries.end()) {
            continue;
        }
        const auto children_it = children_by_parent.find(frn);
        const std::uint32_t child_count =
            children_it == children_by_parent.end() ? 0U : static_cast<std::uint32_t>(children_it->second.size());
        if (!write_binary_tree_node(std::cout, entry_it->second.size, child_count, entry_it->second.is_dir, entry_it->second.name)) {
            std::cerr << "failed to write tree stream node" << std::endl;
            return 1;
        }
        if (children_it != children_by_parent.end()) {
            for (auto child_it = children_it->second.rbegin(); child_it != children_it->second.rend(); ++child_it) {
                stack.push_back(*child_it);
            }
        }
    }
    std::cout.flush();
    write_output_ms = phase_elapsed_ms(phase_started_at);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();
    std::cerr << "{\n";
    std::cerr << "  \"mode\": \"scan-ntfs-tree-stream\",\n";
    std::cerr << "  \"drive\": \"" << json_escape(narrow_ascii(drive_root)) << "\",\n";
    std::cerr << "  \"collect_mft_sizes_ms\": " << collect_mft_sizes_ms << ",\n";
    std::cerr << "  \"collect_entries_ms\": " << collect_entries_ms << ",\n";
    std::cerr << "  \"collect_entries_ioctl_calls\": " << collect_entries_profile.ioctl_calls << ",\n";
    std::cerr << "  \"collect_entries_records_seen\": " << collect_entries_profile.records_seen << ",\n";
    std::cerr << "  \"collect_entries_records_stored\": " << collect_entries_profile.records_stored << ",\n";
    std::cerr << "  \"collect_entries_duplicate_frns\": " << collect_entries_profile.duplicate_frns << ",\n";
    std::cerr << "  \"collect_entries_bytes_returned\": " << collect_entries_profile.bytes_returned << ",\n";
    std::cerr << "  \"collect_entries_ioctl_ms\": " << collect_entries_profile.ioctl_ms << ",\n";
    std::cerr << "  \"collect_entries_parse_ms\": " << collect_entries_profile.parse_ms << ",\n";
    std::cerr << "  \"collect_entries_name_ms\": " << collect_entries_profile.name_ms << ",\n";
    std::cerr << "  \"collect_entries_store_ms\": " << collect_entries_profile.store_ms << ",\n";
    std::cerr << "  \"rollup_sizes_ms\": " << rollup_sizes_ms << ",\n";
    std::cerr << "  \"build_children_ms\": " << build_children_ms << ",\n";
    std::cerr << "  \"sort_children_ms\": " << sort_children_ms << ",\n";
    std::cerr << "  \"write_output_ms\": " << write_output_ms << ",\n";
    std::cerr << "  \"elapsed_ms\": " << static_cast<std::uint64_t>(elapsed_ms) << "\n";
    std::cerr << "}\n";
    return 0;
}

int run_scan_ntfs_root_summary(const std::wstring& drive_root, const std::wstring& out_path) {
    const std::wstring volume_path = make_volume_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    const PrivilegeStatus manage_volume{};
    const PrivilegeStatus backup{};
    const PrivilegeStatus restore{};

    HANDLE volume = CreateFileW(
        volume_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );

    if (volume == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open raw volume: " << GetLastError() << std::endl;
        return 1;
    }

    NtfsVolumeLayout layout = query_ntfs_volume_layout(volume);
    if (!layout.ok) {
        const DWORD error = layout.error;
        CloseHandle(volume);
        std::cerr << "failed to query ntfs layout: " << error << std::endl;
        return 1;
    }

    DWORD error_out = 0;
    std::vector<std::uint64_t> size_by_record;
    if (!collect_raw_mft_sizes(volume, layout, size_by_record, error_out)) {
        CloseHandle(volume);
        std::cerr << "failed to collect raw mft sizes: " << error_out << std::endl;
        return 1;
    }

    std::unordered_map<std::uint64_t, NtfsSummaryEntry> entries;
    std::vector<std::uint64_t> order;
    entries.reserve(4'500'000);
    order.reserve(4'500'000);
    if (!collect_ntfs_entries(volume, size_by_record, entries, order, error_out)) {
        CloseHandle(volume);
        std::cerr << "failed to collect ntfs entries: " << error_out << std::endl;
        return 1;
    }
    CloseHandle(volume);

    for (auto it = order.rbegin(); it != order.rend(); ++it) {
        auto child_it = entries.find(*it);
        if (child_it == entries.end()) {
            continue;
        }
        auto parent_it = entries.find(child_it->second.parent_frn);
        if (parent_it != entries.end()) {
            parent_it->second.size += child_it->second.size;
        }
    }

    std::vector<NtfsSummaryEntry> root_children;
    root_children.reserve(128);
    std::uint64_t total_size = 0;
    for (const auto& frn : order) {
        auto it = entries.find(frn);
        if (it == entries.end()) {
            continue;
        }
        if (entries.find(it->second.parent_frn) != entries.end()) {
            continue;
        }
        root_children.push_back(it->second);
        total_size += it->second.size;
    }

    std::sort(
        root_children.begin(),
        root_children.end(),
        [](const NtfsSummaryEntry& left, const NtfsSummaryEntry& right) { return left.size > right.size; }
    );

    std::ofstream out(narrow_ascii(out_path), std::ios::binary | std::ios::trunc);
    if (!out) {
        std::cerr << "failed to open output file" << std::endl;
        return 1;
    }
    for (const auto& entry : root_children) {
        out << "S\t"
            << (entry.is_dir ? 1 : 0) << '\t'
            << entry.size << '\t'
            << json_escape(narrow_ascii(entry.name))
            << '\n';
    }
    out.flush();
    out.close();

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "scan-ntfs-root-summary");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_bool("se_manage_volume_enabled", manage_volume.enabled);
    print_json_bool("se_backup_enabled", backup.enabled);
    print_json_bool("se_restore_enabled", restore.enabled);
    print_json_number("root_children", root_children.size());
    print_json_number("total_size", total_size);
    print_json_string("summary_path", json_escape(narrow_ascii(out_path)));
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

int run_bench_mft_read(const std::wstring& drive_root, std::uint64_t bytes_to_read) {
    const std::wstring mft_path = make_mft_file_path(drive_root);
    const auto started_at = std::chrono::steady_clock::now();

    enable_privilege(L"SeBackupPrivilege");
    enable_privilege(L"SeRestorePrivilege");

    HANDLE handle = CreateFileW(
        mft_path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_SEQUENTIAL_SCAN,
        nullptr
    );

    if (handle == INVALID_HANDLE_VALUE) {
        std::cerr << "failed to open $MFT: " << GetLastError() << std::endl;
        return 1;
    }

    LARGE_INTEGER file_size{};
    if (!GetFileSizeEx(handle, &file_size)) {
        const DWORD error = GetLastError();
        CloseHandle(handle);
        std::cerr << "failed to query $MFT size: " << error << std::endl;
        return 1;
    }

    const std::uint64_t target = (bytes_to_read == 0 || bytes_to_read > static_cast<std::uint64_t>(file_size.QuadPart))
        ? static_cast<std::uint64_t>(file_size.QuadPart)
        : bytes_to_read;

    std::vector<BYTE> buffer(1024 * 1024);
    std::uint64_t total_read = 0;
    DWORD read_error = 0;
    while (total_read < target) {
        const DWORD chunk = static_cast<DWORD>(std::min<std::uint64_t>(buffer.size(), target - total_read));
        DWORD bytes_read = 0;
        if (!ReadFile(handle, buffer.data(), chunk, &bytes_read, nullptr)) {
            read_error = GetLastError();
            break;
        }
        if (bytes_read == 0) {
            break;
        }
        total_read += bytes_read;
    }

    CloseHandle(handle);

    const auto finished_at = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(finished_at - started_at).count();

    std::cout << "{\n";
    print_json_string("mode", "bench-mft-read");
    print_json_string("drive", json_escape(narrow_ascii(drive_root)));
    print_json_number("target_bytes", target);
    print_json_number("bytes_read", total_read);
    print_json_number("read_error", read_error);
    print_json_number("elapsed_ms", static_cast<std::uint64_t>(elapsed_ms), false);
    std::cout << "}\n";
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::cerr << "usage:\n"
                  << "  ntfs_usn_probe.exe probe C:\\\n"
                  << "  ntfs_usn_probe.exe scan-win32 C:\\ output.tsv\n"
                  << "  ntfs_usn_probe.exe scan-ntfs-root-summary C:\\ output.tsv\n"
                  << "  ntfs_usn_probe.exe scan-usn C:\\ output.tsv\n"
                  << "  ntfs_usn_probe.exe scan-ntfs C:\\ output.tsv\n"
                  << "  ntfs_usn_probe.exe bench-file-sizes C:\\ 10000\n"
                  << "  ntfs_usn_probe.exe probe-mft-file C:\\\n"
                  << "  ntfs_usn_probe.exe bench-mft-read C:\\ 104857600\n"
                  << "  ntfs_usn_probe.exe bench-raw-mft-sizes C:\\ 100000\n";
        return 2;
    }

    const std::wstring mode = argv[1];
    if (mode == L"probe") {
        if (argc < 3) {
            std::cerr << "probe requires a drive path" << std::endl;
            return 2;
        }
        return run_probe(argv[2]);
    }
    if (mode == L"scan-win32") {
        if (argc < 4) {
            std::cerr << "scan-win32 requires a root path and output file" << std::endl;
            return 2;
        }
        return run_scan_win32(argv[2], argv[3]);
    }
    if (mode == L"scan-usn") {
        if (argc < 4) {
            std::cerr << "scan-usn requires a drive path and output file" << std::endl;
            return 2;
        }
        return run_scan_usn(argv[2], argv[3]);
    }
    if (mode == L"scan-ntfs-root-summary") {
        if (argc < 4) {
            std::cerr << "scan-ntfs-root-summary requires a drive path and output file" << std::endl;
            return 2;
        }
        return run_scan_ntfs_root_summary(argv[2], argv[3]);
    }
    if (mode == L"scan-ntfs") {
        if (argc < 4) {
            std::cerr << "scan-ntfs requires a drive path and output file" << std::endl;
            return 2;
        }
        return run_scan_ntfs(argv[2], argv[3]);
    }
    if (mode == L"scan-ntfs-tree-stream") {
        if (argc < 3) {
            std::cerr << "scan-ntfs-tree-stream requires a drive path" << std::endl;
            return 2;
        }
        return run_scan_ntfs_tree_stream(argv[2]);
    }
    if (mode == L"bench-file-sizes") {
        if (argc < 4) {
            std::cerr << "bench-file-sizes requires a drive path and sample limit" << std::endl;
            return 2;
        }
        return run_bench_file_sizes(argv[2], _wcstoui64(argv[3], nullptr, 10));
    }
    if (mode == L"probe-mft-file") {
        if (argc < 3) {
            std::cerr << "probe-mft-file requires a drive path" << std::endl;
            return 2;
        }
        return run_probe_mft_file(argv[2]);
    }
    if (mode == L"bench-mft-read") {
        if (argc < 4) {
            std::cerr << "bench-mft-read requires a drive path and byte count" << std::endl;
            return 2;
        }
        return run_bench_mft_read(argv[2], _wcstoui64(argv[3], nullptr, 10));
    }
    if (mode == L"bench-raw-mft-sizes") {
        if (argc < 4) {
            std::cerr << "bench-raw-mft-sizes requires a drive path and sample limit" << std::endl;
            return 2;
        }
        return run_bench_raw_mft_sizes(argv[2], _wcstoui64(argv[3], nullptr, 10));
    }

    // Backward compatible default: treat a bare drive as probe target.
    if (argc == 2) {
        return run_probe(argv[1]);
    }

    std::cerr << "unknown mode" << std::endl;
    return 2;
}
