// AAPT2's Windows narrow-path API rejects this workspace's Chinese absolute
// path. Keep every file in place, but pass project-relative ASCII paths.
#include <windows.h>
#include <process.h>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
#include <iostream>
namespace fs=std::filesystem;

static void replace_all(std::string& value,const std::string& from,const std::string& to) {
    size_t p=0;while((p=value.find(from,p))!=std::string::npos){value.replace(p,from.size(),to);p+=to.size();}
}

int wmain(int argc,wchar_t** argv) {
    wchar_t executable[32768];GetModuleFileNameW(nullptr,executable,32768);
    fs::path root=fs::path(executable).parent_path().parent_path().parent_path().parent_path();
    if(!SetCurrentDirectoryW(root.c_str()))return 91;
    std::wstring prefix=root.wstring()+L"\\";
    std::vector<std::wstring> values{L".cache\\phase2\\native_tools\\aapt2_64.exe"};
    for(int i=1;i<argc;i++) {
        std::wstring original=argv[i];bool response=original.size()>1&&original[0]==L'@';
        std::wstring name=response?original.substr(1):original;
        for(wchar_t& c:name)if(c==L'/')c=L'\\';
        if(_wcsnicmp(name.c_str(),prefix.c_str(),prefix.size())==0)name=name.substr(prefix.size());
        if(response) {
            std::ifstream input(fs::path(name),std::ios::binary);
            if(!input)return 92;
            std::string body((std::istreambuf_iterator<char>(input)),{});
            replace_all(body,root.u8string()+"\\","");
            replace_all(body,root.generic_u8string()+"/","");
            fs::path local=fs::path(L".tmp/phase2/java")/(L"aapt-args-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(i)+L".txt");
            std::ofstream output(local,std::ios::binary);output<<body;output.close();
            values.push_back(L"@"+local.wstring());
        } else {
            // Retain switches/identifiers exactly; normalize only paths that
            // pointed inside the project, avoiding accidental flag changes.
            values.push_back(name.size()<original.size()?name:original);
        }
    }
    std::vector<const wchar_t*> raw;for(const auto& value:values)raw.push_back(value.c_str());raw.push_back(nullptr);
    intptr_t result=_wspawnv(_P_WAIT,values[0].c_str(),raw.data());
    if(result==-1){std::cerr<<"AAPT2 wrapper spawn failed errno="<<errno<<"\n";return 93;}
    return static_cast<int>(result);
}
