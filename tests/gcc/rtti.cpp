struct Base {
    virtual ~Base() {}
};

struct Derived : Base {};

int main() {
    Derived derived;
    Base *base = &derived;
    return dynamic_cast<Derived *>(base) ? 0 : 1;
}
