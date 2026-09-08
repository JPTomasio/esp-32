// Mocks minimos do ambiente Arduino/ESP32 para testar a logica no PC.
#pragma once
#include <cstdio>
#include <cstdarg>
#include <string>
#include <map>

#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2

// String do Arduino: construivel a partir de numeros e concatenavel com +.
struct String {
  std::string s;
  String(){}
  String(const char* v): s(v){}
  String(const std::string& v): s(v){}
  String(int v): s(std::to_string(v)){}
  String(unsigned long v): s(std::to_string(v)){}
  String(long v): s(std::to_string(v)){}
  String operator+(const String& o) const { return String(s + o.s); }
  String& operator+=(const String& o){ s += o.s; return *this; }
  const char* c_str() const { return s.c_str(); }
  bool operator==(const char* o) const { return s == o; }
};
inline String operator+(const char* a, const String& b){ return String(a) + b; }

// --- tempo controlado pelo teste ---
extern unsigned long g_millis;
inline unsigned long millis(){ return g_millis; }
inline void delay(unsigned long ms){ g_millis += ms; }
inline void delayMicroseconds(unsigned int){}

// --- pinos controlados pelo teste ---
extern std::map<int,int> g_pinos;
extern int g_escritas_buzzer_high;
extern int g_pino_buzzer_observado;
inline void pinMode(int,int){}
inline int digitalRead(int p){ auto it=g_pinos.find(p); return it==g_pinos.end()?HIGH:it->second; }
inline void digitalWrite(int p,int v){
  if(p==g_pino_buzzer_observado && v==HIGH) g_escritas_buzzer_high++;
  g_pinos[p]=v;
}

// --- Serial ---
struct SerialMock {
  bool quieto = true;
  void begin(long){}
  void println(){ if(!quieto) printf("\n"); }
  void println(const char* s){ if(!quieto) printf("%s\n", s); }
  void print(const char* s){ if(!quieto) printf("%s", s); }
  void print(const String& s){ if(!quieto) printf("%s", s.c_str()); }
  void println(const String& s){ if(!quieto) printf("%s\n", s.c_str()); }
  void printf(const char* f, ...){
    if(quieto) return;
    va_list a; va_start(a,f); vprintf(f,a); va_end(a);
  }
};
extern SerialMock Serial;

// --- WiFi / HTTP (sempre offline no teste) ---
#define WL_CONNECTED 3
#define WIFI_STA 1
struct WiFiMock {
  int estado = 0;  // nunca conectado no teste
  void mode(int){}
  void begin(const char*, const char*){}
  int status(){ return estado; }
  String localIP(){ return "0.0.0.0"; }
  void reconnect(){}
};
extern WiFiMock WiFi;

struct HTTPClient {
  void begin(const char*){}
  void addHeader(const char*, const char*){}
  void setTimeout(int){}
  int POST(const String&){ return -1; }
  String errorToString(int){ return "offline"; }
  void end(){}
};
